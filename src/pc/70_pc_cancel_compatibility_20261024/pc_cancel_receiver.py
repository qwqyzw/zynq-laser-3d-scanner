"""Frozen61 receiver with cancellation keyed by immutable run UUID.

A restarted coordinator has a new session ID. Discard must still remove the
matching received run and close only viewers belonging to that run.
"""
from pathlib import Path
import json,os,re,sys
P=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(P/'61_current_axis_laser_release_20261024'))
import pc_receiver_cancel as receiver
import pc_discard

def compatible_discard(identity,receipts,output):
    if not re.fullmatch('[0-9a-f]{32}_[0-9a-f]{32}',identity):raise ValueError('Invalid discard identity')
    rid=identity.split('_')[1];output=Path(output).resolve();keys={identity}
    keys.update(k for k in receipts if re.fullmatch('[0-9a-f]{32}_'+rid,k))
    if output.is_dir():keys.update(p.name for p in output.iterdir() if re.fullmatch('[0-9a-f]{32}_'+rid,p.name))
    manifests=set();closed=0
    for key in keys:
        receipt=receipts.pop(key,{})
        if receipt:closed+=int(pc_discard.close_owned_viewer(receipt))
        if re.fullmatch('[0-9a-f]{64}',receipt.get('manifest_sha256','')):manifests.add(receipt['manifest_sha256'])
        target=output/key
        # Validate the source manifest before deriving cache paths. Never scan
        # arbitrary folders or infer paths from untrusted filenames.
        manifest=target/'viewer/export_manifest.json'
        if manifest.is_file() and not manifest.is_symlink():
            import hashlib
            manifests.add(hashlib.sha256(manifest.read_bytes()).hexdigest())
        pc_discard.remove_child(output,target)
    still_referenced={r.get('manifest_sha256') for r in receipts.values()}
    if os.environ.get('LOCALAPPDATA'):
        base=Path(os.environ['LOCALAPPDATA'])/'ZynqPointCloud'
        for digest in manifests-still_referenced:
            for folder in ['cloudcompare_cache','display_receipts']:
                pc_discard.remove_child(base/folder,base/folder/digest)
    return {'cancelled_scan_removed':identity,'matched_run_UUID':rid,'matching_identities_removed':sorted(keys),
            'owned_viewers_closed':closed,'pc_coordinate_computation':False}

def main():
    pc_discard.discard_local=compatible_discard
    if '--use-local-connector' in sys.argv:
        # Only the existing private connector knows authentication. No password
        # is read, logged or included in this distributable source file.
        sys.argv.remove('--use-local-connector')
        sys.path.insert(0,str(P/'results/button_scan_20261020'));import board_ops
        receiver.connect=lambda args,password:board_ops.connect()
        # Suppress password prompting; the connector handles trusted auth.
        if '--identity-file' not in sys.argv:sys.argv.extend(['--identity-file','connector-managed'])
    return receiver.main()
if __name__=='__main__':raise SystemExit(main())
