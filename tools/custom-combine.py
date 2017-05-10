#!/usr/bin/env python
"""Simple script provides coverage combining across build chains."""
# pylint: disable=invalid-name
from __future__ import print_function

# local
import tempfile
import shutil
import glob
import os

# 3rd-party
import coverage
import six

PROJ_ROOT = os.path.join(os.path.dirname(__file__), os.pardir)
COVERAGERC = os.path.join(PROJ_ROOT, '.coveragerc')


def main():
    """Program entry point."""
    cov = coverage.Coverage(config_file=COVERAGERC)
    cov.combine()

    # Duplicate coverage files, coverage.py unconditionally deletes them
    # on .combine(), we wish to keep each file in perpetuity.
    with tempfile.mkdtemp() as dst_folder:
        data_paths = []
        for src in glob.glob(os.path.join(PROJ_ROOT, '._coverage.*')):
            dst = os.path.join(dst_folder, os.path.basename(src))
#                src.replace('._coverage', '.coverage')))
            shutil.copy(src, dst)
            data_paths.append(dst)

    cov.combine(data_paths=data_paths, strict=True)
    cov.save()
    cov.html_report(ignore_errors=False)
    print("--> {magenta}open {proj_root}/htmlcov/index.html{normal}"
          " for review.".format(magenta='\x1b[1;35m', normal='\x1b[0m',
                                proj_root=os.path.relpath(PROJ_ROOT)))

    fout = six.StringIO()
    cov.report(file=fout, ignore_errors=True)
    for line in fout.getvalue().splitlines():
        if u'TOTAL' in line:
            total_line = line
            break
    else:
        raise ValueError("'TOTAL' summary not found in summary output")

    _, no_stmts, no_miss, _ = total_line.split(None, 3)
    no_covered = int(no_stmts) - int(no_miss)

    print("##teamcity[buildStatisticValue "
          "key='CodeCoverageAbsLTotal' "
          "value='{0}']".format(no_stmts))
    print("##teamcity[buildStatisticValue "
          "key='CodeCoverageAbsLCovered' "
          "value='{0}']".format(no_covered))

if __name__ == '__main__':
    main()
