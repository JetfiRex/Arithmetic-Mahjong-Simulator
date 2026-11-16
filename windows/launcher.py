"""Frozen entry point; dispatch multiprocessing before importing the desktop app."""

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--window-self-test":
        from singleplayer.window_check import check_window
        raise SystemExit(check_window(sys.argv[2]))

    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from singleplayer.desktop import self_test
        raise SystemExit(self_test(sys.argv[2]))

    from singleplayer.desktop import main
    main()
