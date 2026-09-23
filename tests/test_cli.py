"""Command-line parsing: global options work before or after the subcommand."""

from vacation_finder.cli import DEFAULT_CONFIG, build_parser


def parse(*argv):
    return build_parser().parse_args(list(argv))


def test_verbose_after_subcommand():
    # The scheduled workflows run `track --verbose` and `digest --verbose`.
    assert parse("track", "--verbose").verbose is True
    assert parse("digest", "-v").verbose is True


def test_options_before_subcommand_still_work():
    args = parse("--verbose", "--dry-run", "digest")
    assert args.verbose is True
    assert args.dry_run is True


def test_subcommand_does_not_reset_options_given_before_it():
    args = parse("--config", "other.yaml", "--out", "x.html", "digest")
    assert args.config == "other.yaml"
    assert args.out == "x.html"


def test_defaults_when_omitted():
    args = parse("track")
    assert args.verbose is False
    assert args.dry_run is False
    assert args.config == DEFAULT_CONFIG


def test_dry_run_after_subcommand():
    args = parse("digest", "--dry-run", "--out", "build/p.html")
    assert args.dry_run is True
    assert args.out == "build/p.html"
