"""Run the RGB model trial without visible demo manipulation steps."""
if __package__:
    from .run_demo_trial import main
else:
    from run_demo_trial import main


if __name__ == '__main__':
    raise SystemExit(main(demo_motion=False))
