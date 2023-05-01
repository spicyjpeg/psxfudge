# -*- coding: utf-8 -*-

from pathlib import Path

def _addExecutable(name, script):
	module, func = script.split(":", 1)
	launcher     = Path(workpath).joinpath(f"{name}.py")

	# Ugly workaround, but it works.
	with launcher.open("wt") as _file:
		_file.write(f"from {module} import {func}\n{func}()\n")

	analysis   = Analysis(( launcher, ))
	executable = EXE(
		PYZ(analysis.pure, analysis.zipped_data),
		analysis.scripts,
		name    = name,
		console = True,
		strip   = False,
		upx     = False
	)

	return executable, analysis.binaries, analysis.zipfiles, analysis.datas

COLLECT(
	*_addExecutable("fudgebundle", "psxfudge.cli.bundle:fudgebundle"),
	*_addExecutable("fudgetim",    "psxfudge.cli.tim:fudgetim"),
	name  = "psxfudge",
	strip = False,
	upx   = False
)
