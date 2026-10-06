# Shortcuts for the two KiCad versions in the image, started in the
# background so the terminal stays usable. An optional argument is the
# project file: kicad_stable board.kicad_pro
#
# Both go through AppRun rather than bin/kicad. AppRun links the bundled
# WebKit helper programs to /tmp/.kicad-wk-helpers, the fixed path WebKit was
# built with, and without that link KiCad dies as soon as it shows an HTML
# page - which the template chooser behind "New project" does.
kicad_stable()  { nohup "$KICAD_STABLE_APPDIR/AppRun" kicad "$@" >/dev/null 2>&1 & disown; }
kicad_nightly() { nohup "$KICAD_NIGHTLY_APPDIR/AppRun" kicad "$@" >/dev/null 2>&1 & disown; }
