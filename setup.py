#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools   import setup, Extension
from Cython.Build import cythonize
from numpy        import get_include

setup(
	packages = [
		"psxfudge"
	],
	ext_modules = cythonize([
		Extension(
			"psxfudge._native",
			[
				"psxfudge/_native.pyx",
				"libimagequant/blur.c",
				"libimagequant/kmeans.c",
				"libimagequant/libimagequant.c",
				"libimagequant/mediancut.c",
				"libimagequant/mempool.c",
				"libimagequant/nearest.c",
				"libimagequant/pam.c"
				#"libimagequant/remap.c"
			],
			include_dirs = [
				get_include(),
				"libimagequant"
			]
		)
	])
)
