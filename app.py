
import feedparser
from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime
import sqlite3
import threading
import time
import logging
import traceback

# Set up logging
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# Predefined categories
CATEGORIES = [
    'General', 'Technology', 'Business', 'Sports', 'Health', 
    'Science', 'Entertainment', 'Politics', 'Education'
]

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
        # Add category column to feeds table
        c.execute('''
            CREATE TABLE IF NOT EXISTS feeds
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             url TEXT UNIQUE NOT NULL,
             title TEXT,
             category TEXT,
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
        
        # Add category column to existing feeds if it doesn't exist
        try:
            c.execute('ALTER TABLE feeds ADD COLUMN category TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
            
        conn.commit()

@app.route('/')
def index():
    try:
        # Get filter parameters
        selected_feeds = request.args.getlist('feeds')
        selected_categories = request.args.getlist('categories')
        
        with get_db() as conn:
            c = conn.cursor()
            
            # Get all feeds
            c.execute('SELECT id, url, title, category FROM feeds')
            feeds = c.fetchall()
            
            # Get all used categories
            c.execute('SELECT DISTINCT category FROM feeds WHERE category IS NOT NULL')
            used_categories = [row['category'] for row in c.fetchall()]
            
            # Build the entry query based on filters
            query = '''
                SELECT 
                    e.id,
                    e.title,
                    e.link,
                    e.description,
                    e.published,
                    f.title as feed_title,
                    f.category as feed_category,
                    f.id as feed_id
                FROM entries e
                JOIN feeds f ON e.feed_id = f.id
                WHERE 1=1
            '''
            params = []
            
            if selected_feeds:
                query += ' AND f.id IN ({})'.format(','.join(['?' for _ in selected_feeds]))
                params.extend(selected_feeds)
            
            if selected_categories:
                query += ' AND f.category IN ({})'.format(','.join(['?' for _ in selected_categories]))
                params.extend(selected_categories)
            
            query += ' ORDER BY e.published DESC LIMIT 50'
            
            c.execute(query, params)
            entries = c.fetchall()
            
            return render_template('index.html', 
                                 feeds=feeds,
                                 entries=entries,
                                 categories=CATEGORIES,
                                 used_categories=used_categories,
                                 selected_feeds=selected_feeds,
                                 selected_categories=selected_categories)
    
    except Exception as e:
        logger.error(f"Error in index route: {str(e)}")
        logger.error(traceback.format_exc())
        return f"An error occurred loading the page: {str(e)}", 500

@app.route('/add_feed', methods=['POST'])
def add_feed():
    url = request.form.get('url')
    category = request.form.get('category')
    
    if url:
        try:
            # First verify the feed is valid
            feed = feedparser.parse(url)
            if feed.bozo:
                flash(f"Error parsing feed: {feed.bozo_exception}")
                return redirect(url_for('index'))
            
            with get_db() as conn:
                c = conn.cursor()
                c.execute('INSERT INTO feeds (url, category, last_updated) VALUES (?, ?, ?)',
                         (url, category, datetime.now()))
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

@app.route('/update_feed/<int:feed_id>', methods=['POST'])
def update_feed_category(feed_id):
    category = request.form.get('category')
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('UPDATE feeds SET category = ? WHERE id = ?', (category, feed_id))
            conn.commit()
        flash("Feed category updated successfully!")
    except Exception as e:
        logger.error(f"Error updating feed category: {str(e)}")
        flash(f"Error updating feed category: {str(e)}")
    
    return redirect(url_for('index'))

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