import argparse
import logging
import os
import sys

from . import app, config


def main():
    p = argparse.ArgumentParser(prog="amifeed", description="Plain text RSS proxy for retro computers.")
    p.add_argument("-c", "--config", default=os.environ.get("AMIFEED_CONFIG", "amifeed.toml"),
                   help="TOML settings file (default: amifeed.toml, or $AMIFEED_CONFIG)")
    p.add_argument("-v", "--verbose", action="store_true", help="log every request")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = config.load(args.config)
    except (OSError, config.ConfigError) as e:
        sys.exit("amifeed: %s" % e)
    try:
        app.serve(cfg)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
