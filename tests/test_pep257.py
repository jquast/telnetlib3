# std
import os
import glob

# 3rd-party
import pep257


def test_pep257_conformance():
    glob_proj = (os.path.dirname(__file__), '..', 'telnetlib3', '*.py',)
    files = map(os.path.relpath, glob.glob(os.path.join(*glob_proj)))
    all_errors = pep257.check(files)
    num_errors = 0
    for num_errors, err in enumerate(all_errors):
        print(err)
    assert num_errors == 0
