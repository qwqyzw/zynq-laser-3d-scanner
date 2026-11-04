"""Same original overlay/ownership loader, with separately bound fast coordinator."""
import argparse
import json
from pathlib import Path
import sys

from fast_button_service import Coordinator, inspect_config, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--load-board', action='store_true')
    parser.add_argument('--serve-board', action='store_true')
    args = parser.parse_args()
    config, artifacts = inspect_config(args.config)
    config['config_file'] = args.config.resolve()
    if args.load_board:
        from load_button_overlay import load_board
        print(json.dumps(load_board(config, artifacts), ensure_ascii=False), flush=True)
    elif not args.serve_board:
        print(json.dumps({'status': 'fast_button_boot_configuration_verified', 'version': VERSION,
                          'fast_manifest_sha256': artifacts['fast_release']['manifest_sha256'],
                          'hardware_accessed': False, 'hardware_acceptance': False}))
    if args.serve_board:
        Coordinator(config, artifacts).serve()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Fast button startup rejected: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
