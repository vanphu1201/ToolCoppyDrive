import sqlite3
import os
from datetime import datetime

class UsageDatabase:
    """Simple SQLite database to track user usage and payment status."""
    
    def __init__(self, db_path=None):
        # Use /tmp for serverless environments (Vercel, AWS Lambda, etc.)
        if db_path is None:
            # Check if running on Vercel/serverless (read-only filesystem)
            if os.path.exists('/tmp'):
                db_path = '/tmp/usage.db'
            else:
                db_path = 'usage.db'
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database and create tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_usage (
                client_id TEXT PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                is_paid INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_user(self, client_id):
        """Get user data by client_id."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM user_usage WHERE client_id = ?', (client_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'client_id': row[0],
                'usage_count': row[1],
                'is_paid': bool(row[2]),
                'created_at': row[3],
                'updated_at': row[4]
            }
        return None
    
    def create_user(self, client_id):
        """Create a new user record."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        try:
            cursor.execute('''
                INSERT INTO user_usage (client_id, usage_count, is_paid, created_at, updated_at)
                VALUES (?, 0, 0, ?, ?)
            ''', (client_id, now, now))
            conn.commit()
        except sqlite3.IntegrityError:
            # User already exists
            pass
        finally:
            conn.close()
    
    def increment_usage(self, client_id):
        """Increment usage count for a user."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        cursor.execute('''
            UPDATE user_usage 
            SET usage_count = usage_count + 1, updated_at = ?
            WHERE client_id = ?
        ''', (now, client_id))
        
        conn.commit()
        conn.close()
    
    def mark_as_paid(self, client_id):
        """Mark user as paid."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        cursor.execute('''
            UPDATE user_usage 
            SET is_paid = 1, updated_at = ?
            WHERE client_id = ?
        ''', (now, client_id))
        
        conn.commit()
        conn.close()
    
    def get_or_create_user(self, client_id):
        """Get user or create if doesn't exist."""
        user = self.get_user(client_id)
        if not user:
            self.create_user(client_id)
            user = self.get_user(client_id)
        return user
