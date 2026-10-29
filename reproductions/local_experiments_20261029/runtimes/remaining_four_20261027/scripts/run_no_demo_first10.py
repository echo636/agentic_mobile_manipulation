"""Run the first ten RGB model tasks without visible demo manipulation steps."""
if __package__:
    from .run_demo_first10 import main
else:
    from run_demo_first10 import main


if __name__ == '__main__':
    raise SystemExit(main(demo_motion=False))
