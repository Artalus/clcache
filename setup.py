#!/usr/bin/env python

import sys
if sys.version_info < (3,5):
    sys.exit('Python < 3.5 is not supported')

from setuptools import setup, find_packages

setup(
    name='clcache',
    description='MSVC compiler cache',
    author='Frerich Raabe',
    author_email='raabe@froglogic.com',
    url='https://github.com/frerich/clcache',
    packages=find_packages(),
    platforms='any',
    keywords=[],
    install_requires=[
        'atomicwrites',
        'pymemcache',
        'pyuv',
    ],
    extras_require={
        'installer': [
            'pyinstaller',
        ],
        'test': [
            'pytest',
            'pytest-cov',
            'pytest-xdist',
        ],
    },
    entry_points={
        'console_scripts': [
            'clcache = clcache.__main__:mainWrapper',
            'clcache-server = clcache.server.__main__:main',
        ]
    },
    setup_requires=[
        'setuptools_scm',
    ],
    data_files=[
        ('', ('clcache.pth',)),
    ],
    use_scm_version=True)
