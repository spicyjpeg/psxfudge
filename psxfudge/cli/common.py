# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

import sys, re, logging, json
from collections import ChainMap
from pathlib     import Path
from argparse    import ArgumentParser, FileType, Action

from ..util     import parseJSON, CaseDict
from ..__init__ import __version__ as LIBRARY_VERSION
from .__init__  import __version__ as CLI_VERSION

## Default properties for all tools

IMAGE_PROPERTIES = {
	"match":      ".*",
	"frames":     "0-0xffff",
	"mipLevels":  1,
	"crop":       ( 0, 0, 0x10000, 0x10000 ),
	"scale":      1.0,
	"mipScale":   0.5,
	"bpp":        4,
	"palette":    "auto",
	"dither":     0,
	"scaleMode":  "lanczos",
	"alphaRange": ( 0x20, 0xe0 ),
	"blackValue": ( 1, 1, 1, 0 ),
	"cropMode":   "none",
	"padding":    0,
	"flipMode":   "none"
}

TIM_IMAGE_PROPERTIES = {
	"imagePos":   ( 0, 0 ),
	"palettePos": ( 0, 0 )
}

SOUND_PROPERTIES = {
	"channels":   1,
	"sampleRate": 0,
	"interleave": 0x6800,
	"loopOffset": -1.0
}

STRING_TABLE_PROPERTIES = {
	"encoding": "ascii",
	"align":    4
}

## Private utilities

def _getOutputFileDesc(placeholders):
	if not placeholders:
		return "Path to file to be generated"
	if len(placeholders) == 1:
		return f"Path to file(s) to be generated; {{{placeholders[0]}}} placeholder can be specified for multiple files"

	names = ", ".join(f"{{{name}}}" for name in placeholders)
	return f"Path to file(s) to be generated; {names} placeholders can be specified for multiple files"

class _ListPropertiesAction(Action):
	def __init__(self, nargs = 0, defaults = None, **namedArgs):
		super().__init__(nargs = 0, **namedArgs)
		self.defaults = defaults

	def __call__(self, parser, namespace, values, optionString):
		maxLength  = max(map(len, self.defaults.keys()))
		properties = "\n".join(
			f"  {key.ljust(maxLength)} = {json.dumps(value)}"
			for key, value in self.defaults.items()
		)

		parser.exit(0, f"Default property values:\n{properties}\n")

## Main classes for command-line tools

class Tool:
	"""
	Internal base class for all PSXFudge command-line tools.
	"""

	def __init__(self, name, description, defaults = None):
		self.defaults = defaults
		self.parser   = ArgumentParser(
			prog         = name,
			description  = description,
			epilog       = "This tool is part of the PSXFudge toolkit.",
			add_help     = False,
			allow_abbrev = False
			#fromfile_prefix_chars = "@"
		)

	def __call__(self, argv = None):
		args = self._parseArgs(argv)

		self.run(
			args,
			self._parseProperties(args.set, args.properties, self.defaults)
		)

	def addToolOptions(self):
		group = self.parser.add_argument_group("Tool options")

		group.add_argument(
			"-h", "--help",
			action = "help",
			help   = "Show this help message and exit"
		)
		group.add_argument(
			"-V", "--version",
			action  = "version",
			help    = "Show version information and exit",
			version = f"PSXFudge {LIBRARY_VERSION}, PSXFudge CLI {CLI_VERSION}"
		)
		group.add_argument(
			"-L", "--list-properties",
			action   = _ListPropertiesAction,
			help     = "List all supported properties and their default values then exit",
			defaults = self.defaults
		)
		group.add_argument(
			"-v", "--verbose",
			action = "count",
			help   = "Increase logging verbosity (-v = info, -vv = debug)"
		)

		return group

	def addConfigOptions(self):
		group = self.parser.add_argument_group("Configuration options")

		group.add_argument(
			"-s", "--set",
			action  = "append",
			type    = str,
			help    = "Set the value of a property (use JSON syntax to specify value)",
			metavar = "property=value"
		)
		group.add_argument(
			"-p", "--properties",
			type    = FileType("rt"),
			help    = "Load properties from the root object of the specified JSON file",
			metavar = "file"
		)

		return group

	def addFileOptions(self, placeholders = None):
		group = self.parser.add_argument_group("File paths")

		group.add_argument(
			"inputFile",
			type  = Path,
			nargs = "+",
			help  = "Paths to input files"
		)
		group.add_argument(
			"outputFile",
			type = Path,
			help = _getOutputFileDesc(placeholders)
		)

		return group

	def addPackerOptions(self):
		group = self.parser.add_argument_group("Texture packing options")

		group.add_argument(
			"-D", "--discard-step",
			type    = int,
			default = 1,
			help    = "How tightly to pack texture atlases (default is 1); increase to improve packing speed",
			metavar = "1-128"
		)
		group.add_argument(
			"-T", "--try-splits",
			action = "store_true",
			help   = "Attempt to use multiple criteria when subdividing free space (may worsen packing ratio!)"
		)
		group.add_argument(
			"-P", "--preserve-palettes",
			action = "store_true",
			help   = "Disable deduplicating 'similar' palettes (only deduplicate palettes that are actually identical)"
		)
		group.add_argument(
			"-A", "--dump-atlas",
			type    = Path,
			help    = "Save all generated texture pages as a single image to specified path for inspection",
			metavar = "path"
		)

		return group

	def _parseArgs(self, argv):
		args = self.parser.parse_args(argv)

		logging.basicConfig(
			format = "[%(funcName)-13s %(levelname)-7s] %(message)s",
			level  = (
				logging.WARNING,
				logging.INFO,    # -v
				logging.DEBUG    # -vv
			)[min(args.verbose or 0, 2)]
		)
		return args

	def _parseProperties(self, args, _file = None, defaults = None):
		properties = CaseDict(defaults)

		if _file:
			with _file:
				try:
					properties.update(parseJSON(_file.read()))
				except:
					self.parser.error(f"failed to parse properties from {_file.name}")
		if args:
			for arg in args:
				try:
					key, value      = arg.split("=", 1)
					properties[key] = json.loads(value)
				except:
					self.parser.error(f"invalid property specification: {arg}")

		return properties

	def run(self, args, properties):
		pass

class MultiEntryTool(Tool):
	"""
	Internal base class for all PSXFudge command-line tools that process
	multiple entries (such as bundle entries) at a time and take one or more
	JSON lists of entries as input.
	"""

	def __call__(self, argv = None):
		args      = self._parseArgs(argv)
		entryList = []

		for _file in args.configFile:
			with _file:
				_list = parseJSON(_file.read())
			if type(_list) is not list:
				self.parser.error(f"the root element of {_file.name} is not a list")

			entryList.extend(_list)

		self.run(
			args,
			entryList,
			self._parseProperties(args.set, args.properties, self.defaults),
			self._parseProperties(args.force_set)
		)

	def addConfigOptions(self):
		group = self.parser.add_argument_group("Configuration options")

		group.add_argument(
			"-s", "--set",
			action  = "append",
			type    = str,
			help    = "Set the default value of a property if not specified in an entry (use JSON syntax to specify value)",
			metavar = "property=value"
		)
		group.add_argument(
			"-S", "--force-set",
			action  = "append",
			type    = str,
			help    = "Override a property for all entries, ignoring values in the config file (use JSON syntax to specify value)",
			metavar = "property=value"
		)
		group.add_argument(
			"-p", "--properties",
			type    = FileType("rt"),
			help    = "Load default properties for all entries from the root object of the specified JSON file",
			metavar = "file"
		)

		return group

	def addFileOptions(self, placeholders = None):
		group = self.parser.add_argument_group("File paths")

		group.add_argument(
			"configFile",
			type  = FileType("rt"),
			nargs = "+",
			help  = "Paths to JSON files containing a list of entry objects"
		)
		group.add_argument(
			"outputFile",
			type = Path,
			help = _getOutputFileDesc(placeholders)
		)

		return group

	def run(self, args, entryList, defaults, forced):
		pass
