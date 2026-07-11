-- Apple News to Readwise Scraper
-- Run this script while viewing an article in Apple News

-- Get the script directory dynamically
set scriptDir to do shell script "dirname " & quoted form of (POSIX path of (path to me))
set scriptPath to scriptDir & "/scrape_to_readwise.py"

-- Run with uv (using which to find uv location)
set uvPath to do shell script "which uv || echo $HOME/.local/bin/uv"
do shell script uvPath & " run --with requests --with beautifulsoup4 --with readability-lxml --with lxml " & quoted form of scriptPath

display notification "Done!" with title "Apple News Scraper"
