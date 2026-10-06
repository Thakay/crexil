#!/usr/bin/env python3
"""A thin artist/agent client of the shared Crexil HTTP actions.

Reads print JSON or the requested CSV summary. Mutations identify the participant and print a delivery ID;
reuse that ID when retrying the same request after a connection failure. Input
JSON describes the same values the artist interface sends. No database or
staging writes happen here. The default target is the isolated preview (scripts/preview.sh).

Examples:
  crexil.py get setup
  crexil.py get operations
  crexil.py get 'operation-requirements?operation_id=OPERATION&revision=REVISION'
  crexil.py --actor agent:Guide post projects --json '{"name":"Character"}'
  crexil.py get projects/PROJECT
  crexil.py get 'projects/PROJECT/record?q=silhouette'
  crexil.py post projects/PROJECT/assessments --json '{"node_ids":["MATERIAL"],"reason":"Good silhouette"}'
  crexil.py get material/MATERIAL/record
  crexil.py import PROJECT reference.png --tool Krita --parents SOURCE
  crexil.py post projects/TARGET/reuse/review --json '{"source_project_id":"SOURCE","node_id":"MATERIAL"}'
  crexil.py post projects/TARGET/reuse --json '{"source_project_id":"SOURCE","node_id":"MATERIAL","review_token":"TOKEN"}'
  crexil.py put directions/DIRECTION/selection --json '{"revision":0,"node_ids":[]}'
  crexil.py get execution/project/PROJECT
  crexil.py post execution/project/PROJECT --json '{"revision":0,"action":"pause"}'
  crexil.py get projects/PROJECT/deliveries.csv
  crexil.py backup PROJECT project.crexil.zip
  crexil.py restore project.crexil.zip --name "Restored project"
"""

from __future__ import annotations

import argparse
import base64
import json
import hashlib
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse


def archive_action(args, actor) -> int:
    request_id = args.request_id or str(uuid.uuid4())
    print(f'Delivery ID: {request_id}', file=sys.stderr)
    base = args.url.rstrip('/') + '/api/crexil/'
    def call(path, values, phase):
        body = {**values, 'actor': actor, 'request_id': str(uuid.uuid5(uuid.NAMESPACE_URL, request_id + ':' + phase))}
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)
    if args.action == 'backup':
        if not args.project.isalnum():
            raise ValueError('Use the project identity from its URL.')
        # Reserve the local filename before asking the server to create work.
        with args.file.open('xb') as output:
            try:
                receipt = call(f'projects/{args.project}/backups', {}, 'backup')
                digest = hashlib.sha256()
                with urllib.request.urlopen(base + f"backups/{receipt['id']}/file", timeout=600) as response:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != receipt['sha256']:
                    raise ValueError('The downloaded backup does not match its receipt.')
            except BaseException:
                args.file.unlink(missing_ok=True)
                raise
        print(json.dumps({'file': str(args.file), 'backup_id': receipt['id'], 'sha256': receipt['sha256'],
                          'unavailable_files': sum(not f['available'] for f in receipt['files'])}, indent=2))
        return 0
    upload = call('archive-uploads', {'filename': args.file.name, 'size_bytes': args.file.stat().st_size}, 'upload')
    with args.file.open('rb') as source:
        request = urllib.request.Request(base + f"archive-uploads/{upload['id']}/file",
            data=iter(lambda: source.read(1024 * 1024), b''), method='PUT',
            headers={'Content-Type': 'application/zip', 'Content-Length': str(args.file.stat().st_size)})
        with urllib.request.urlopen(request, timeout=600) as response:
            ready = json.load(response)
    print(json.dumps(ready['review'], indent=2), file=sys.stderr)
    restored = call(f"archive-uploads/{upload['id']}/restore", {'sha256': ready['sha256'], 'name': args.name}, 'restore')
    print(json.dumps(restored, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8010')
    parser.add_argument('--actor', default='human:Artist', help='human:NAME or agent:NAME')
    parser.add_argument('--request-id', help='Reuse only for identical delivery after a connection failure')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('get', 'post', 'patch', 'put'):
        action_parser = actions.add_parser(action)
        action_parser.add_argument('path', help='Shared action path, such as projects or directions/ID/selection')
        if action != 'get':
            action_parser.add_argument('--json', required=True, help='JSON request body, or @FILE')
    importer = actions.add_parser('import')
    importer.add_argument('project')
    importer.add_argument('file', type=Path)
    importer.add_argument('--parents', nargs='*', default=[])
    importer.add_argument('--tool', default='')
    importer.add_argument('--explanation', default='')
    backup = actions.add_parser('backup')
    backup.add_argument('project')
    backup.add_argument('file', type=Path, help='New local backup filename; existing files are never overwritten')
    restore = actions.add_parser('restore')
    restore.add_argument('file', type=Path)
    restore.add_argument('--name')
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1') or url.username:
        parser.error('Use the local Crexil HTTP address.')
    kind, separator, name = args.actor.partition(':')
    if not separator or kind not in ('human', 'agent') or not name.strip():
        parser.error('Identify the participant as human:NAME or agent:NAME.')
    body = None
    try:
        if args.action in ('backup', 'restore'):
            return archive_action(args, {'kind': kind, 'name': name})
        if args.action == 'import':
            path = f'projects/{args.project}/material'
            method = 'POST'
            body = {'filename': args.file.name,
                    'data_base64': base64.b64encode(args.file.read_bytes()).decode(),
                    'parents': args.parents, 'tool': args.tool, 'explanation': args.explanation}
        else:
            path, method = args.path, args.action.upper()
            if method != 'GET':
                source = args.json
                body = json.loads(Path(source[1:]).read_text() if source.startswith('@') else source)
                if not isinstance(body, dict):
                    parser.error('Supply a JSON object for the request.')
        route = urlparse(path)
        decoded_path = unquote(route.path)
        if (route.scheme or route.netloc or decoded_path.startswith('/') or '..' in decoded_path
                or '\\' in decoded_path or route.params or '#' in path
                or (method != 'GET' and route.query)):
            parser.error('Use a relative shared action path without traversal. Query parameters are supported on reads only.')
        if body is not None and not (method == 'POST' and path.endswith('/review')):
            body.update(actor={'kind': kind, 'name': name}, request_id=args.request_id or str(uuid.uuid4()))
            print(f"Delivery ID: {body['request_id']}", file=sys.stderr)
        request = urllib.request.Request(args.url.rstrip('/') + '/api/crexil/' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.headers.get_content_type() == 'text/csv':
                sys.stdout.write(response.read().decode('utf-8'))
            else:
                print(json.dumps(json.load(response), indent=2))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f'{exc}. If delivery was uncertain, inspect the project or retry with the same delivery ID.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
