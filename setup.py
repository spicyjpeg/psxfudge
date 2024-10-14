#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, Extension
from numpy      import get_include

setup(
	ext_modules = [
		Extension(
			"psxfudge.native",
			language      = "c++",
			sources       = [
				"psxfudge/native.pyx",
				"libimagequant/blur.c",
				"libimagequant/kmeans.c",
				"libimagequant/libimagequant.c",
				"libimagequant/mediancut.c",
				"libimagequant/mempool.c",
				"libimagequant/nearest.c",
				"libimagequant/pam.c"
			],
			include_dirs  = [
				get_include(),
				"libimagequant"
			],
			define_macros = [
				( "NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION" )
			]
		)
	]
)
