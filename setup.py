# ------------------------------------------------------------------------------
# Name:          setup.py
# Purpose:       install converter21 package
#
# Authors:       Greg Chapman
#
# Copyright:     (c) 2021-2023 Greg Chapman
# License:       MIT, see LICENSE
# ------------------------------------------------------------------------------

import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='music_site',
        version=0.1,

        description='A music notation site',

        author='Greg Chapman',
        author_email='gregc@mac.com',

        classifiers=[
            'Development Status :: 2 - Pre-Alpha',
            'License :: OSI Approved :: MIT License',
            'Operating System :: OS Independent',
            'Natural Language :: English',
        ],

        packages=setuptools.find_packages(),

        python_requires='>=3.10',

        install_requires=[
            'music21>=9.1',
        ]
    )
