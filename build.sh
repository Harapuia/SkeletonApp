#!/bin/bash
# This script first builds kivy, then the whole app (after kivy is set)

# Variables
CURRENTFOLDERPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
APPLOGO="${CURRENTFOLDERPATH}/images/tribler_applogo.png"
APPSPLASH="${CURRENTFOLDERPATH}/images/splash.jpg"
DIRNAME="TSAP"
PY4APATH="${CURRENTFOLDERPATH}/python-for-android"

# Chat colors
red="\x1B[0;31m"
yellow="\x1B[1;33m"
green="\x1B[0;32m"
NC="\x1B[0m" # No Color

while getopts ":p:" opt; do
	case $opt in
		p)
			PY4APATH=$OPTARG
			;;
	esac
done

if [ "X$PY4APATH" == "X" ]; then
	echo -e "${yellow} Please give the path of python-for-android using the -p flag${NC}"
	exit 1
fi

#echo -e "${green}COPYING main.py TO TRIBLER DIRECTORY${NC}"
#cp "${CURRENTFOLDERPATH}/tsap/main.py" "${CURRENTFOLDERPATH}/tribler/main.py"

# If the folders do not exist, we try to create them and throw an error.
if [ ! -e "${CURRENTFOLDERPATH}/images/" ]; then
	# Throw an error since folders are missing
	echo -e "${red}You need to have a folder ./images/ aborting.${NC}"
	echo -e "${red}The missing folders will be made now, but images will be missing!${NC}"
	mkdir -p ".//images/"
	exit 1
fi

# Check if the app icon isn't missing
if [ ! -f $APPLOGO ]; then
	echo -e "${red}${APPLOGO} is missing! Aborting.${NC}"
	exit 1
fi

# Check if the splashscreen isn't missing
if [ ! -f $APPSPLASH ]; then
	echo -e "${red}${APPSPLASH} is missing! Aborting.${NC}"
	exit 1
fi

# If the app folder in AT3 does not exist, create it.
if [ ! -e "${CURRENTFOLDERPATH}/app" ]; then
	echo -e "${red}${CURRENTFOLDERPATH}/app does not exist! Attempting to create it${NC}"
	mkdir -p "${CURRENTFOLDERPATH}/app"
else
	rm -rv "${CURRENTFOLDERPATH}/app/*"
fi

if [ ! -f "${CURRENTFOLDERPATH}/tsap/main.py" ]; then
	echo -e "${red}${CURRENTFOLDERPATH}/tsap/main.py is missing{$NC}" {
	exit
fi

# Check if destination exist
if [ -e "${PY4APATH}/dist/${DIRNAME}" ]; then
	echo -e "${yellow}The distribution ${PY4APATH}/dist/${DIRNAME} already exist${NC}"
	echo -e "${yellow}Press a key to remove it, or Control + C to abort.${NC}"
	read
	rm -rf "${PY4APATH}/dist/${DIRNAME}"
fi

# Build kivy first
#pushd $PY4APATH
#./distribute.sh -m "kivy" -d $DIRNAME
#popd

# Remove the created directory, the build/libs folder and the build/python folder
rm -rf "${PY4APATH}/dist/${DIRNAME}"
rm -rf "${PY4APATH}/build/libs"
rm -rf "${PY4APATH}/build/python"

# Adapt sdl_main.c to export the correct JNI function (PythonService_nativeSetEnv)
mv "${PY4APATH}/src/jni/sdl_main/sdl_main.c" "${PY4APATH}/src/jni/sdl_main/sdl_main.c.bak"
sed s/SDLSurfaceView_nativeSetEnv/PythonService_nativeSetEnv/ "${PY4APATH}/src/jni/sdl_main/sdl_main.c.bak" > "${PY4APATH}/src/jni/sdl_main/sdl_main.c"
rm "${PY4APATH}/src/jni/sdl_main/sdl_main.c.bak"

# Build a distribute folder with all the packages now that kivy has been set
pushd $PY4APATH
#./distribute.sh -m "openssl pycrypto m2crypto sqlite3 pyasn1 dispersy netifaces Tribler" -d $DIRNAME
./distribute.sh -m "openssl pycrypto m2crypto sqlite3 pyasn1 netifaces Tribler libtorrent" -d $DIRNAME
popd

# FIXME: copy precompiled swift binary
cp "${CURRENTFOLDERPATH}/swift.arm" "${CURRENTFOLDERPATH}/tsap/swift"

# Build apk
cd "${PY4APATH}/dist/${DIRNAME}/"
./build.py --package org.tsap.tribler.full --name "a-TSAP Tribler" --version 0.9 --dir "${CURRENTFOLDERPATH}/tsap/service" debug --permission ACCESS_NETWORK_STATE --permission ACCESS_WIFI_STATE --permission INTERNET --icon $APPLOGO --presplash $APPSPLASH

# FIXME: rm precompiled swift binary
rm "${CURRENTFOLDERPATH}/tsap/swift"

# Copy the .so files to the libs folder in tsap
find "${PY4APATH}/dist/${DIRNAME}/libs/armeabi" -type f -name '*.so' -exec cp {} "${CURRENTFOLDERPATH}/../tsap/libs/armeabi-v7a" \;

# Copy the assets MP3s to the assets folder in tsap
mkdir -p "${CURRENTFOLDERPATH}/../tsap/assets"
find "${PY4APATH}/dist/${DIRNAME}/assets" -type f -name '*.mp3' -exec cp {} "${CURRENTFOLDERPATH}/../tsap/assets" \;

# Change the version strings to the correct values
private_version=$(grep -oP '(?<=<string name="private_version">)\d*.\d*(?=</string>)' "${PY4APATH}/dist/${DIRNAME}/res/values/strings.xml")

public_version=$(grep -oP '(?<=<string name="public_version">)\d*.\d*(?=</string>)' "${PY4APATH}/dist/${DIRNAME}/res/values/strings.xml")

echo "<?xml version=\"1.0\" encoding=\"utf-8\"?>
<resources>

    <string name=\"private_version\">$private_version</string>
    <string name=\"public_version\">$public_version</string>

</resources>" > "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml"

#mv "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml" "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak"
#perl -pe "s/<string name=\"private_version\">\d*.\d*<\/string>/<string name=\"private_version\">$private_version<\/string>/" "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak" > "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml"
#rm "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak"

#mv "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml" "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak"
#perl -pe "s/<string name=\"public_version\">\d*.\d*<\/string>/<string name=\"public_version\">$public_version<\/string>/" "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak" > "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml"
#rm "${CURRENTFOLDERPATH}/../tsap/res/values/asset_versions.xml.bak"

# Copy the .apk files to our own app folder
#find "${PY4APATH}/dist/${DIRNAME}/bin" -type f -name '*.apk' -exec cp {} "${CURRENTFOLDERPATH}/app" \;

echo -e "${green}All done!${NC} Everything seems to be in order (̿▀̿ ̿Ĺ̯̿̿▀̿ ̿)̄ "
