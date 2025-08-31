import importlib.util
import pytest
import sys
import builtins
import types
import os
import io
import contextlib
from types import ModuleType


SCRIPT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'enable_autotrade.py'))


def load_script_as(name):
    """Load the enable_autotrade script as a module with the given name.
    This ensures each test gets a fresh import context and avoids sys.modules caching.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    # exec_module will run the top-level code (which performs the dynamic imports)
    spec.loader.exec_module(mod)
    return mod


def make_module(mod_name, **attrs):
    m = ModuleType(mod_name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def test_prefers_src_db(monkeypatch):
    # Ensure ADMIN_USER_ID is present
    monkeypatch.setenv('ADMIN_USER_ID', '42')

    called = {}

    def setter(user_id, value):
        called['which'] = 'src.db'
        called['args'] = (user_id, value)

    # Prepare fake modules
    sys.modules['src.db'] = make_module('src.db', set_autotrade=setter)

    # Load script under unique name
    mod = load_script_as('enable_autotrade_test_src_db')

    # Run main and capture output
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()

    out = buf.getvalue()
    assert 'Autotrade enabled' in out
    assert called.get('which') == 'src.db'
    assert called.get('args') == (42, True)


def test_falls_back_to_lunessa(monkeypatch):
    monkeypatch.setenv('ADMIN_USER_ID', '101')

    called = {}

    def setter2(user_id, value):
        called['which'] = 'src.Lunessa_db'
        called['args'] = (user_id, value)

    # Ensure src.db is NOT present and provide Lunessa fallback
    sys.modules.pop('src.db', None)
    sys.modules['src.Lunessa_db'] = make_module('src.Lunessa_db', set_autotrade_status=setter2)

    # Block attempts to import src.db to force the fallback path
    orig_import = builtins.__import__
    def _block(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'src.db' or name.startswith('src.db.'):
            raise ImportError
        return orig_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', _block)

    mod = load_script_as('enable_autotrade_test_lunessa')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()

    out = buf.getvalue()
    assert 'Autotrade enabled' in out
    assert called.get('which') == 'src.Lunessa_db'
    assert called.get('args') == (101, True)


def test_falls_back_to_modules_db_access(monkeypatch):
    monkeypatch.setenv('ADMIN_USER_ID', '7')

    called = {}

    def setter3(user_id, value):
        called['which'] = 'src.modules.db_access'
        called['args'] = (user_id, value)

    # Clean previous entries
    sys.modules.pop('src.db', None)
    sys.modules.pop('src.Lunessa_db', None)

    # Provide package parents to avoid import oddities
    sys.modules.setdefault('src', ModuleType('src'))
    sys.modules.setdefault('src.modules', ModuleType('src.modules'))

    # Insert the deep module
    sys.modules['src.modules.db_access'] = make_module('src.modules.db_access', set_autotrade_status=setter3)

    # Block earlier imports to force reaching src.modules.db_access
    orig_import = builtins.__import__
    def _block(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ('src.db', 'src.Lunessa_db') or name.startswith('src.db.') or name.startswith('src.Lunessa_db.'):
            raise ImportError
        return orig_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', _block)

    mod = load_script_as('enable_autotrade_test_mdb')
    # Sanity: ensure the loaded module picked our fallback module
    assert getattr(mod, 'db', None) is not None
    assert getattr(mod.db, '__name__', '') == 'src.modules.db_access'
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()

    out = buf.getvalue()
    assert 'Autotrade enabled' in out
    assert called.get('which') == 'src.modules.db_access'
    assert called.get('args') == (7, True)


def test_no_module_causes_exit(monkeypatch):
    # Remove any fake modules
    for k in list(sys.modules.keys()):
        if k.startswith('src') and (k == 'src' or k.startswith('src.')):
            sys.modules.pop(k, None)

    # Importing the script without any DB helper should raise SystemExit at import time
    import importlib

    # Block all three candidate imports so the script exits during import
    orig_import = builtins.__import__
    def _block_all(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ('src.db', 'src.Lunessa_db', 'src.modules.db_access') or name.startswith('src.'):
            raise ImportError
        return orig_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', _block_all)

    with pytest.raises(SystemExit):
        load_script_as('enable_autotrade_test_none')
