#!/usr/bin/env python
import datetime
import hashlib
import re
import requests
import argparse
import sys
import json
import logging
from sqlalchemy import (
    create_engine,
    Column,
    String,
    DateTime,
    Integer,
    Text,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship
from flask import Flask, Response
from werkzeug.middleware.proxy_fix import ProxyFix
import xml.etree.ElementTree as ET

# Logging boilerplate
log = logging.getLogger(__name__)
log_handler = logging.StreamHandler(sys.stderr)
log.addHandler(log_handler)

Base = declarative_base()

class LWNFetchArticles(Base):
    __tablename__ = 'lwn_fetch_articles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    articles = relationship("LWNArticle", back_populates="fetch_info")

class LWNArticle(Base):
    __tablename__ = 'lwn_articles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    headline = Column(String, nullable=False)
    url = Column(Text, nullable=False)
    subject = Column(String, nullable=False)
    date = Column(DateTime, nullable=False)
    article_hash = Column(String(16), nullable=False, unique=True)
    fetch_info_id = Column(Integer, ForeignKey('lwn_fetch_articles.id'), nullable=False)

    fetch_info = relationship("LWNFetchArticles", back_populates="articles")
    free_status = relationship("LWNFetchFreeStatus", back_populates="article")

    __table_args__ = (UniqueConstraint('article_hash', name='uq_article_hash'),)

    @staticmethod
    def generate_hash(headline: str, date: datetime.datetime) -> str:
        date_str = date.strftime('%Y-%m-%d')
        hash_input = f"{headline}{date_str}".encode()
        return hashlib.sha256(hash_input).hexdigest()[:16]

class LWNFetchFreeStatus(Base):
    __tablename__ = 'lwn_fetch_free_status'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    article_id = Column(Integer, ForeignKey('lwn_articles.id'), nullable=False)
    status_code = Column(Integer, nullable=False)

    article = relationship("LWNArticle", back_populates="free_status")

class LWNFeedScraper:
    def __init__(self, db_url: str):
        log.debug(f"Initializing database connection to {db_url}")
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        self.Session = scoped_session(sessionmaker(bind=self.engine))

    def initialize_database(self):
        """Initialize the database with necessary tables."""
        log.info("Initializing the database schema...")
        Base.metadata.create_all(self.engine)
        log.info("Database initialized successfully.")

    def scrape_headlines(self):
        """Scrape the headlines from LWN.net."""
        log.info("Starting scraping of headlines...")
        response = requests.get("https://lwn.net/headlines/text")
        if not response.ok:
            log.error(f"Failed to fetch headlines: {response.status_code}")
            raise Exception(f"Failed to fetch headlines: {response.status_code}")

        lines = response.text.split("\n")
        line_i = 0

        def expect_line(expected: str):
            nonlocal line_i
            if lines[line_i] != expected:
                log.error(f"Expected {repr(expected)}, got {repr(lines[line_i])}")
                raise Exception(f"Expected {repr(expected)}, got {repr(lines[line_i])}")
            line_i += 1

        def read_line() -> str:
            nonlocal line_i
            line = lines[line_i]
            line_i += 1
            return line

        def peek_line() -> str:
            return lines[line_i]

        expect_line("This is the LWN.net text headlines file.")
        expect_line("&&")

        headlines = []
        while line_i < len(lines):
            if not peek_line():
                read_line()
                continue

            headline = read_line()
            url = read_line()
            meta = read_line()
            subject, datestr = re.match(r'^([A-Za-z]+), (.+)$', meta).groups()
            date = datetime.datetime.strptime(datestr, '%b %d, %Y %M:%S %Z (%a)')
            expect_line("&&")

            headlines.append((headline, url, subject, date))

        log.info(f"Scraped {len(headlines)} articles successfully.")
        self._store_to_db(headlines)
        return headlines

    def _store_to_db(self, articles):
        """Store articles in the database, ensuring deduplication."""
        log.debug("Storing articles in the database...")
        session = self.Session
        fetch_info = LWNFetchArticles()
        session.add(fetch_info)
        session.commit()

        for headline, url, subject, date in articles:
            article_hash = LWNArticle.generate_hash(headline, date)
            if not session.query(LWNArticle).filter_by(article_hash=article_hash).first():
                article = LWNArticle(
                    headline=headline,
                    url=url,
                    subject=subject,
                    date=date,
                    article_hash=article_hash,
                    fetch_info=fetch_info,
                )
                session.add(article)
        session.commit()
        log.debug("Articles stored successfully.")
        self.Session.remove()

    def _update_free_status(self):
        """Check which articles are free and update their status."""
        log.info("Updating free status for articles...")
        session = self.Session
        articles = session.query(LWNArticle).all()
        free_updates = []

        for article in articles:
            if session.query(LWNFetchFreeStatus).filter_by(article_id=article.id, status_code=200).first():
                continue

            try:
                response = requests.get(article.url, timeout=5)
                fetch_status = LWNFetchFreeStatus(
                    article_id=article.id, status_code=response.status_code
                )
                session.add(fetch_status)
                if response.status_code == 200:
                    free_updates.append(article.headline)
            except requests.RequestException:
                fetch_status = LWNFetchFreeStatus(
                    article_id=article.id, status_code=500
                )
                session.add(fetch_status)

        session.commit()
        log.info(f"Free status updated for {len(free_updates)} articles.")
        self.Session.remove()
        return free_updates

def generate_atom_feed(articles, title="LWN Feed", link="https://lwn.net"):
    """Generate an ATOM feed from a list of articles."""
    import xml.etree.ElementTree as ET

    feed = ET.Element("feed", xmlns="http://www.w3.org/2005/Atom")
    ET.SubElement(feed, "title").text = title
    ET.SubElement(feed, "link", href=link, rel="self")
    ET.SubElement(feed, "updated").text = datetime.datetime.utcnow().isoformat() + "Z"

    for article in articles:
        entry = ET.SubElement(feed, "entry")
        ET.SubElement(entry, "title").text = article.headline
        ET.SubElement(entry, "link", href=article.url)
        ET.SubElement(entry, "id").text = article.article_hash
        ET.SubElement(entry, "updated").text = article.date.isoformat() + "Z"
        ET.SubElement(entry, "summary").text = article.subject

    return ET.tostring(feed, encoding="utf-8", method="xml")

def create_app(scraper):
    """Create a Flask application for serving ATOM feeds."""
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app)

    @app.route('/lwn.xml', methods=['GET'])
    def get_all_articles():
        """Serve all articles as an ATOM feed."""
        log.info("Serving all articles as an ATOM feed...")
        session = scraper.Session
        articles = session.query(LWNArticle).all()
        scraper.Session.remove()
        feed = generate_atom_feed(articles, title="LWN Articles")
        return Response(feed, mimetype="application/atom+xml")

    @app.route('/lwn_free.xml', methods=['GET'])
    def get_free_articles():
        """Serve free articles as an ATOM feed."""
        log.info("Serving free articles as an ATOM feed...")
        session = scraper.Session
        articles = session.query(LWNArticle).filter(
            LWNArticle.free_status.any(LWNFetchFreeStatus.status_code == 200)
        ).all()
        scraper.Session.remove()
        feed = generate_atom_feed(articles, title="LWN Free Articles")
        return Response(feed, mimetype="application/atom+xml")

    return app

def main():
    parser = argparse.ArgumentParser(description="LWN Feed Scraper CLI")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARN", "ERROR"], default="INFO", help="Set the logging level")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Initialize subcommand
    init_parser = subparsers.add_parser("initialize", help="Initialize the database")

    # Scrape subcommand
    scrape_parser = subparsers.add_parser("scrape", help="Scrape articles")
    
    # Serve subcommand
    serve_parser = subparsers.add_parser("serve", help="Start the server")
    serve_group = serve_parser.add_mutually_exclusive_group(required=True)
    serve_group.add_argument("--listen-address", help="Address to listen on")
    serve_group.add_argument("--listen-socket", help="Unix socket to listen on")

    args = parser.parse_args()

    # Configure logging level
    log.setLevel(getattr(logging, args.log_level.upper()))
    log_handler.setLevel(getattr(logging, args.log_level.upper()))

    scraper = LWNFeedScraper(args.db)

    if args.command == "initialize":
        scraper.initialize_database()

    elif args.command == "scrape":
        try:
            articles = scraper.scrape_headlines()
            free_updates = scraper._update_free_status()
            summary = {
                "scraped": len(articles),
                "free_updates": len(free_updates),
            }
            print(json.dumps(summary, indent=4))
            sys.exit(0)
        except Exception as e:
            log.error(f"Error during scraping: {e}")
            sys.exit(1)

    elif args.command == "serve":
        app = create_app(scraper)
        if args.listen_address:
            host, port = args.listen_address.split(":")
            app.run(host=host, port=int(port))
        elif args.listen_socket:
            app.run(unix_socket=args.listen_socket)

if __name__ == "__main__":
    main()
