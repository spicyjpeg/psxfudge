#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import logging, json
from struct   import Struct
from argparse import FileType

import av
from ._avenc import convertAudioStream
from ._util  import alignToMultiple, ArgParser

DEFAULT_OPTIONS = {
	"chunklength": 0x6800,
	"samplerate":  44100,
	"channels":    2,
	"loopoffset":  -1.0
}

VAG_HEADER_STRUCT  = Struct("> 4s I 4x 2I 12x 16s")
VAG_HEADER_MAGIC   = b"VAGp"
VAG_HEADER_VERSION = 3

## Main

def _createParser():
	parser = ArgParser("Converts a source audio file into a stream file.")

	group = parser.add_argument_group("Debugging options")
	group.add_argument(
		"-V", "--vag",
		action = "store_true",
		help   = "Add dummy .VAG header at the beginning of the stream (for debugging)"
	)

	group = parser.add_argument_group("Configuration options")
	group.add_argument(
		"-s", "--set",
		action  = "append",
		type    = str,
		help    = "Set a property (use JSON syntax to specify value)",
		metavar = "property=value"
	)

	group = parser.add_argument_group("File paths")
	group.add_argument(
		"inputFile",
		type = av.open,
		help = "Path to source file (in any format supported by FFmpeg)"
	)
	group.add_argument(
		"outputFile",
		type = FileType("wb"),
		help = "Path to stream file to be generated"
	)

	return parser

def main():
	parser = _createParser()
	args   = parser.parse_args()

	options = DEFAULT_OPTIONS.copy()

	for arg in (args.set or []):
		key, value = arg.split("=", 1)
		options[key.strip().lower()] = json.loads(value)

	with args.outputFile as _file:
		if args.vag:
			_file.write(bytes(VAG_HEADER_STRUCT.size))

		dataLength = 0

		for chunk in convertAudioStream(args.inputFile, options):
			dataLength += _file.write(alignToMultiple(chunk, 2048))

		if args.vag:
			header = VAG_HEADER_STRUCT.pack(
				VAG_HEADER_MAGIC,
				VAG_HEADER_VERSION,
				dataLength,
				options["samplerate"],
				b"fudgestreamdebug"
			)

			_file.seek(0)
			_file.write(header)

if __name__ == "__main__":
	main()
