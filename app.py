import feedparser
from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime
import sqlite3
import threading
import time
import logging
import traceback  # Added for detailed error tracking

# Set up logging
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

def dict_factory(cursor, row):
    fields = [column[0] for column in cursor.description]
    return {key: value for key, value in zip(fields, row)}

def get_db():
    conn = sqlite3.connect('rss_feeds.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = dict_factory
    return conn

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        # Updated schema with explicit timestamp handling
        c.execute('''
            CREATE TABLE IF NOT EXISTS feeds
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             url TEXT UNIQUE NOT NULL,
             title TEXT,
             last_updated TIMESTAMP)
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS entries
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             feed_id INTEGER,
             title TEXT,
             link TEXT,
             description TEXT,
             published TIMESTAMP,
             FOREIGN KEY (feed_id) REFERENCES feeds (id))
        ''')
        conn.commit()

@app.route('/')
def index():
    try:
        logger.debug("Starting index route")
        with get_db() as conn:
            c = conn.cursor()
            
            # Get all feeds
            logger.debug("Fetching feeds")
            c.execute('SELECT id, url, title FROM feeds')
            feeds = c.fetchall()
            logger.debug(f"Found {len(feeds)} feeds")
            
            # Get recent entries with explicit columns
            logger.debug("Fetching entries")
            c.execute('''
                SELECT 
                    e.id,
                    e.title,
                    e.link,
                    e.description,
                    e.published,
                    f.title as feed_title
                FROM entries e
                JOIN feeds f ON e.feed_id = f.id
                ORDER BY e.published DESC
                LIMIT 50
            ''')
            entries = c.fetchall()
            logger.debug(f"Found {len(entries)} entries")
            
            # Debug print a sample entry
            if entries:
                logger.debug(f"Sample entry: {entries[0]}")
            
            return render_template('index.html', feeds=feeds, entries=entries)
    
    except Exception as e:
        logger.error(f"Error in index route: {str(e)}")
        logger.error(traceback.format_exc())  # Print full traceback
        return f"An error occurred loading the page: {str(e)}", 500

def update_single_feed(feed_id, feed_url):
    logger.debug(f"Updating feed: {feed_url}")
    try:
        feed = feedparser.parse(feed_url)
        
        if feed.bozo:
            logger.error(f"Feed parsing error for {feed_url}: {feed.bozo_exception}")
            return False
            
        with get_db() as conn:
            c = conn.cursor()
            
            # Update feed title
            feed_title = feed.feed.get('title', feed_url)
            c.execute('UPDATE feeds SET title = ?, last_updated = ? WHERE id = ?',
                     (feed_title, datetime.now(), feed_id))
            
            # Add new entries
            for entry in feed.entries:
                # Get published date
                published = None
                if 'published_parsed' in entry:
                    published = entry.published_parsed
                elif 'updated_parsed' in entry:
                    published = entry.updated_parsed
                
                if published:
                    published = datetime(*published[:6])
                else:
                    published = datetime.now()
                
                # Clean and truncate description if needed
                description = entry.get('description', entry.get('summary', ''))
                if len(description) > 1000:
                    description = description[:1000] + '...'
                
                try:
                    c.execute('''
                        INSERT OR IGNORE INTO entries
                        (feed_id, title, link, description, published)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        feed_id,
                        entry.get('title', 'No title'),
                        entry.get('link', ''),
                        description,
                        published
                    ))
                except sqlite3.Error as e:
                    logger.error(f"Error inserting entry: {str(e)}")
                    continue
            
            conn.commit()
            logger.debug(f"Successfully updated feed: {feed_url}")
            return True
            
    except Exception as e:
        logger.error(f"Error updating feed {feed_url}: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def update_feeds():
    while True:
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT id, url FROM feeds')
                feeds = c.fetchall()
            
            for feed in feeds:
                update_single_feed(feed['id'], feed['url'])
                time.sleep(1)  # Small delay between feed updates
                
        except Exception as e:
            logger.error(f"Error in update_feeds: {str(e)}")
            logger.error(traceback.format_exc())
            
        time.sleep(60)  # Update every minute

@app.route('/add_feed', methods=['POST'])
def add_feed():
    url = request.form.get('url')
    if url:
        try:
            # First verify the feed is valid
            feed = feedparser.parse(url)
            if feed.bozo:
                flash(f"Error parsing feed: {feed.bozo_exception}")
                return redirect(url_for('index'))
            
            with get_db() as conn:
                c = conn.cursor()
                c.execute('INSERT INTO feeds (url, last_updated) VALUES (?, ?)',
                         (url, datetime.now()))
                feed_id = c.lastrowid
                conn.commit()
            
            # Immediately update the feed
            if update_single_feed(feed_id, url):
                flash("Feed added and updated successfully!")
            else:
                flash("Feed added but there was an error updating it.")
                
        except sqlite3.IntegrityError:
            flash("This feed has already been added!")
        except Exception as e:
            logger.error(f"Error adding feed: {str(e)}")
            logger.error(traceback.format_exc())
            flash(f"Error adding feed: {str(e)}")
    
    return redirect(url_for('index'))

@app.route('/remove_feed/<int:feed_id>')
def remove_feed(feed_id):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('DELETE FROM entries WHERE feed_id = ?', (feed_id,))
            c.execute('DELETE FROM feeds WHERE id = ?', (feed_id,))
            conn.commit()
        flash("Feed removed successfully!")
    except Exception as e:
        logger.error(f"Error removing feed: {str(e)}")
        logger.error(traceback.format_exc())
        flash(f"Error removing feed: {str(e)}")
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    # Start feed updater in background
    update_thread = threading.Thread(target=update_feeds, daemon=True)
    update_thread.start()
    app.run(debug=True, host="0.0.0.0", port=3000)