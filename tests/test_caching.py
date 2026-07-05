#!/usr/bin/env python3

import os
import shutil
import tempfile

import helpers_test  # noqa: F401  # side effect: make cparser importable
from cparser import caching


def test_caching_parse_roundtrip():
    # ``caching.parse`` is used by external projects (e.g. PySDL) as their
    # parser entry point.  Cold parse fills the cache; a second parse of
    # the unchanged file must hit it and apply the cached state.
    tmpdir = tempfile.mkdtemp(prefix="cparser_caching_test_")
    old_caching_dir = caching.CACHING_DIR
    caching.CACHING_DIR = os.path.join(tmpdir, "cache") + "/"
    hdr = os.path.join(tmpdir, "test_hdr.h")
    with open(hdr, "w") as f:
        f.write("""
        #define ANSWER 42
        typedef struct { int a; short b; } Foo;
        int add(int x, int y);
        """)

    hits = []
    orig_check = caching.check_cache
    def counting_check(stateStruct, full_filename):
        r = orig_check(stateStruct, full_filename)
        hits.append(r is not None)
        return r
    caching.check_cache = counting_check

    try:
        s1 = caching.parse(hdr)
        assert "Foo" in s1.typedefs
        assert not any(hits)  # cold: no cache yet

        hits.clear()
        s2 = caching.parse(hdr)
        assert any(hits), "second parse did not hit the cache"
        assert "Foo" in s2.typedefs
        assert "add" in s2.funcs
        assert "ANSWER" in s2.macros
    finally:
        caching.check_cache = orig_check
        caching.CACHING_DIR = old_caching_dir
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    helpers_test.main(globals())
