#!/usr/bin/env python3
"""Package committed Pi server source after an authorized release verification."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if git('status', '--porcelain', '--untracked-files=no'):
        raise SystemExit('Commit tracked source changes before packaging')
    root = Path(git('rev-parse', '--show-toplevel'))
    version = next(ast.literal_eval(node.value) for node in
                   ast.parse((root / 'house_audio_server.py').read_text()).body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == 'SERVICE_VERSION'
                       for target in node.targets))
    sha = git('rev-parse', 'HEAD')
    prefix = f'HouseMusic_Server_v{version}'
    files = ['INSTALL_CD.txt', 'install-cd.sh', 'rollback-cd.sh', 'install.sh',
             'house_audio_server.py', 'auto_cd.py', 'input_sources.py',
             'radio_history.py', 'radio_listening.py', 'radio_metadata.py',
             'radio_stations.py', 'state_file.py', 'subwoofers.py',
             'docs/RELEASE_0.14.7.md', 'docs/RELEASE_0.14.6.md', 'docs/RELEASE_0.14.5.md',
             'docs/SOURCES_EXPANSION_PLAN_2026-11-23.md',
             'docs/WEBPAGE_UPDATE_PLAN_2026-11-23.md',
             'docs/UNMUTED_SESSION_RETENTION.md',
             'docs/STARTUP_TIMING_AND_MPD_BATCHES.md']
    files.extend(git('ls-files', 'config', 'web', 'systemd', 'pi_input').splitlines())
    files = sorted(set(files))
    payload = {name: (root / name).read_bytes() for name in files}
    for name, content in payload.items():
        committed = subprocess.check_output(['git', 'show', f'{sha}:{name}'])
        if content != committed:
            raise SystemExit(f'File differs from committed source: {name}')
    for installer in ('install.sh', 'install-cd.sh'):
        if f'SERVICE_VERSION = "{version}"'.encode() not in payload[installer]:
            raise SystemExit(f'{installer} version guard does not match source')
    manifest = dict(version=version, source_sha=sha,
                    source_tree=git('rev-parse', 'HEAD^{tree}'),
                    files_sha256={name: hashlib.sha256(data).hexdigest()
                                  for name, data in payload.items()})
    payload['SOURCE_SHA.txt'] = (sha + '\n').encode()
    payload['MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir / f'{prefix}_Pi.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as zip_file:
        for name, data in payload.items():
            zip_file.writestr(f'{prefix}/{name}', data)
    with ZipFile(archive) as zip_file:
        if zip_file.testzip() is not None:
            raise SystemExit('ZIP integrity check failed')
        for name, data in payload.items():
            if zip_file.read(f'{prefix}/{name}') != data:
                raise SystemExit(f'ZIP content mismatch: {name}')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output_dir / f'{archive.name}.sha256').write_text(f'{digest}  {archive.name}\n')
    print(json.dumps(dict(file=str(archive.resolve()), bytes=archive.stat().st_size,
                          source_sha=sha, sha256=digest, files=len(payload))))


if __name__ == '__main__':
    main()
