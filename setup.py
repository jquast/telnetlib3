#! /usr/bin/env python
"""
Distribution file for telnetlib3
"""
import sys
import os
import io

from setuptools.command.test import test as TestCommand
from pip.req import parse_requirements
from distutils.core import setup


class PyTest(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        import pytest
        errcode = pytest.main(self.test_args)
        sys.exit(errcode)

here = os.path.abspath(os.path.dirname(__file__))
readme_rst = os.path.join(here, 'README.rst')
long_description = io.open(readme_rst, encoding='utf8').read()
requirements = parse_requirements(os.path.join(here, 'requirements.txt'))
install_requires = [str(req.req) for req in requirements]

setup(name='telnetlib3',
      version='0.2',
      url='http://telnetlib3.rtfd.org/',
      license='ISC',
      author='Jeff Quast',
      description="Telnet server and client Protocol library using asyncio",
      long_description=long_description,
      packages=['telnetlib3', 'telnetlib3.contrib'],
      scripts=['bin/telnet-client', 'bin/telnet-server', 'bin/telnet-talker'],
      #include_package_data=True,
      author_email='contact@jeffquast.com',
      platforms='any',
      keywords=', '.join(('telnet', 'server', 'client', 'bbs', 'mud', 'utf8',
                          'cp437', 'api', 'library', 'asyncio', 'talker',
                          'tulip', )),
      classifiers=['License :: OSI Approved :: ISC License (ISCL)',
                   'Programming Language :: Python :: 3.3',
                   'Programming Language :: Python :: 3.4',
                   'Intended Audience :: Developers',
                   'Development Status :: 4 - Beta',
                   'Topic :: System :: Networking',
                   'Topic :: Terminals :: Telnet',
                   'Topic :: System :: Shells',
                   'Topic :: Internet',
                   ],
      tests_require=['pytest'],
      install_requires=install_requires,
      cmdclass={'test': PyTest},
      extras_require={'testing': ['pytest'], },
      test_suite='tests',
      )
