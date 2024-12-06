# Feeds Set

This project is a set of automatically generated RSS feeds from various points
on the web.

## Usage

```
$ nix build .

$ ./result/bin/feeds-cli --help
usage: feeds-cli [-h] --db DB [--log-level {DEBUG,INFO,WARN,ERROR}] {initialize,scrape,serve} ...

LWN Feed Scraper CLI

positional arguments:
  {initialize,scrape,serve}
    initialize          Initialize the database
    scrape              Scrape articles
    serve               Start the server

options:
  -h, --help            show this help message and exit
  --db DB               Path to SQLite database
  --log-level {DEBUG,INFO,WARN,ERROR}
                        Set the logging level
```
