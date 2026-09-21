from tram.cr_store import ScopeBaseline
from tram.governance.intent_guard import IntentGuard, compile_glob, path_matches


def test_glob_double_star_matches_nested():
    assert path_matches("src/a.py", ["src/**"])
    assert path_matches("src/deep/nested/a.py", ["src/**"])
    assert not path_matches("srcx/a.py", ["src/**"])
    assert not path_matches("a.py", ["src/**"])


def test_glob_star_does_not_cross_slash():
    assert path_matches("a.py", ["*.py"])
    assert not path_matches("src/a.py", ["*.py"])


def test_glob_double_slash_prefix():
    assert path_matches("a.py", ["**/*.py"])
    assert path_matches("x/y/a.py", ["**/*.py"])


def test_dotfile_patterns_keep_leading_dot():
    # regression: leading-dot patterns like ".gitignore" must not be stripped
    assert path_matches(".gitignore", [".gitignore"])
    assert path_matches(".github/workflows/ci.yml", [".github/**"])
    assert not path_matches("gitignore", [".gitignore"])


def test_forbidden_overrides_allowed():
    baseline = ScopeBaseline(allowed_paths=["**"], forbidden_paths=["secrets/**"])
    decision = IntentGuard(baseline).check(["src/a.py", "secrets/k.pem"])
    assert decision.ok is False
    assert decision.violations == ["secrets/k.pem"]
    assert decision.allowed == ["src/a.py"]


def test_guard_ok_when_all_in_scope():
    baseline = ScopeBaseline(allowed_paths=["src/**", "tests/**", "*.py", "README.md"])
    decision = IntentGuard(baseline).check(["src/x.py", "tests/test_x.py", "setup.py", "README.md"])
    assert decision.ok is True
    assert decision.violations == []


def test_compile_glob_anchors():
    pattern = compile_glob("src/**")
    assert pattern.match("src/a.py")
    assert not pattern.match(".hidden/src/a.py")
