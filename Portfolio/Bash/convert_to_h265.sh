#!/bin/bash

# Max number of parallel jobs (adjust based on your CPU)
MAX_JOBS=4

SEASON_DIR="$1"

if [ -z "$SEASON_DIR" ]; then
    echo "Usage: $0 /path/to/season_folder"
    exit 1
fi

if ! command -v HandBrakeCLI &> /dev/null; then
    echo "HandBrakeCLI not found. Please install it first."
    exit 2
fi

# Function to convert a single file
convert_file() {
    INPUT="$1"
    DIRNAME=$(dirname "$INPUT")
    BASENAME=$(basename "$INPUT")
    FILENAME="${BASENAME%.*}"
    TEMP_OUTPUT="${DIRNAME}/${FILENAME}.tmp.mkv"
    FINAL_OUTPUT="${DIRNAME}/${FILENAME}.mkv"

    echo "🎞️  Converting: $INPUT"

    HandBrakeCLI -i "$INPUT" -o "$TEMP_OUTPUT" -e x265 -q 22 -B 160 \
        --optimize --subtitle-lang-list eng --all-subtitles

    if [ $? -eq 0 ]; then
        echo "✅ Success: $FILENAME"
        rm -f "$INPUT"
        mv "$TEMP_OUTPUT" "$FINAL_OUTPUT"
    else
        echo "❌ Failed: $INPUT"
        rm -f "$TEMP_OUTPUT"
    fi
}

export -f convert_file

# Find files and process in parallel
find "$SEASON_DIR" -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" -o -iname "*.mov" \) -print0 |
xargs -0 -n 1 -P "$MAX_JOBS" bash -c 'convert_file "$0"' 

echo "🎉 All parallel conversions complete."
