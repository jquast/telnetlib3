#!/usr/bin/env python
"""Distutils setup script."""
import os
import setuptools


def _get_install_requires(fname):
    return [req_line.strip() for req_line in open(fname)
            if req_line.strip() and not req_line.startswith('#')]


def _get_version(fname):
    import json
    return json.load(open(fname, 'r'))['version']


def _get_long_description(fname):
    import codecs
    return codecs.open(fname, 'r', 'utf8').read()

HERE = os.path.dirname(__file__)

setuptools.setup(
    name='telnetlib3',
    version=_get_version(
        fname=os.path.join(HERE, 'version.json')),
    install_requires=_get_install_requires(
        fname=os.path.join(HERE, 'requirements.txt')),
    long_description=_get_long_description(
        fname=os.path.join(HERE, 'docs', 'intro.rst')),
    description="Telnet server and client Protocol library using asyncio",
    author='Jeff Quast',
    author_email='contact@jeffquast.com',
    platforms='any',
    license='MIT',
    packages=['telnetlib3', 'telnetlib3.contrib', ],
    url='https://github.com/jquast/telnetlib3',
    include_package_data=True,
    zip_safe=True,
    classifiers=[
        'Programming Language :: Python :: 3.4',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: ISC License (ISCL)',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Topic :: Software Development :: Libraries',
        'Topic :: System :: Networking',
        'Topic :: Terminals :: Telnet',
        'Topic :: System :: Shells',
        'Topic :: Internet',
    ],
    keywords=['telnet', 'server', 'client', 'bbs', 'mud', 'utf8',
              'cp437', 'api', 'library', 'asyncio', 'talker',
              'tulip'],
)
