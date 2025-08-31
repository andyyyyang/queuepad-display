#!/bin/bash
# One-click update script for GitHub

cd /Users/andyyang/PycharmProjects/queuepad-display || exit

# Add all changes
git add .

# Commit with timestamp
git commit -m "Update on $(date '+%Y-%m-%d %H:%M:%S')"

# Push to GitHub
git push origin main

echo "✅ Project updated and pushed to GitHub."
