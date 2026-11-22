#!/usr/bin/env bash
# Build the volume button helper (assets/volkeys/VolKeys.java) into assets/volkeys/ks-volkeys.jar, the dex jar
# the tool installs (lck/volkeys.py). The built jar is committed, so running the tool needs no Java or Android SDK.
#   usage: ANDROID_HOME=<sdk> scripts/build-volkeys.sh     (needs platforms;android-35 and build-tools;35.0.0)
set -euo pipefail
SDK=${ANDROID_HOME:-$HOME/Android/Sdk}
here=$(cd "$(dirname "$0")/../assets/volkeys" && pwd)
work=$(mktemp -d)
mkdir -p "$work/classes"
javac --release 8 -cp "$SDK/platforms/android-35/android.jar" -d "$work/classes" "$here/VolKeys.java"
(cd "$work/classes" && jar cf ../classes.jar ks/*.class)
"$SDK/build-tools/35.0.0/d8" --min-api 26 --lib "$SDK/platforms/android-35/android.jar" \
  --output "$work/ks-volkeys.jar" "$work/classes.jar"
cp "$work/ks-volkeys.jar" "$here/ks-volkeys.jar"
unzip -l "$here/ks-volkeys.jar" | grep classes.dex
