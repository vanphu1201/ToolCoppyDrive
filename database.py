import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime

# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

class UsageDatabase:
    """PostgreSQL database to track user usage and payment status."""
    
    def __init__(self):
        # Get database URL from environment variable
        # Vercel Neon uses POSTGRES_URL, custom setups use DATABASE_URL
        self.database_url = os.environ.get('POSTGRES_URL') or os.environ.get('DATABASE_URL')
        
        if not self.database_url:
            # Fallback to SQLite for local development without Postgres
            print("⚠️  No POSTGRES_URL or DATABASE_URL found, using SQLite fallback")
            self._use_sqlite_fallback()
            return
        
        print(f"✅ Using PostgreSQL database (Neon/Vercel)")
        try:
            self._init_db()
        except Exception as e:
            print(f"❌ PostgreSQL connection failed: {e}")
            print("⚠️  Falling back to SQLite")
            self._use_sqlite_fallback()
    
    def _use_sqlite_fallback(self):
        """Fallback to SQLite if PostgreSQL fails"""
        import sqlite3
        self.database_url = None
        self.db_path = '/tmp/usage.db' if os.path.exists('/tmp') else 'usage.db'
        print(f"✅ Using SQLite: {self.db_path}")
        
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
    
    def _init_db(self):
        """Initialize database and create tables if they don't exist."""
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_usage (
                client_id VARCHAR(255) PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                is_paid BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        ''')
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ PostgreSQL database initialized")
    
    def get_user(self, client_id):
        """Get user data by client_id."""
        if not self.database_url:
            # SQLite fallback
            import sqlite3
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
        
        # PostgreSQL
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM user_usage WHERE client_id = %s', (client_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def create_user(self, client_id):
        """Create a new user record."""
        now = datetime.now()
        
        if not self.database_url:
            # SQLite fallback
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO user_usage (client_id, usage_count, is_paid, created_at, updated_at)
                    VALUES (?, 0, 0, ?, ?)
                ''', (client_id, now.isoformat(), now.isoformat()))
                conn.commit()
                print(f"✅ Created new user: {client_id}")
            except sqlite3.IntegrityError:
                conn.rollback()
            finally:
                conn.close()
            return
        
        # PostgreSQL
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO user_usage (client_id, usage_count, is_paid, created_at, updated_at)
                VALUES (%s, 0, FALSE, %s, %s)
            ''', (client_id, now, now))
            conn.commit()
            print(f"✅ Created new user: {client_id}")
        except psycopg2.IntegrityError:
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
    
    def increment_usage(self, client_id):
        """Increment usage count for a user."""
        now = datetime.now()
        
        if not self.database_url:
            # SQLite fallback
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE user_usage 
                SET usage_count = usage_count + 1, updated_at = ?
                WHERE client_id = ?
            ''', (now.isoformat(), client_id))
            conn.commit()
            conn.close()
            print(f"✅ Incremented usage for {client_id}")
            return
        
        # PostgreSQL
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE user_usage 
            SET usage_count = usage_count + 1, updated_at = %s
            WHERE client_id = %s
        ''', (now, client_id))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Incremented usage for {client_id}")
    
    def mark_as_paid(self, client_id):
        """Mark user as paid."""
        now = datetime.now()
        
        if not self.database_url:
            # SQLite fallback
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE user_usage 
                SET is_paid = 1, updated_at = ?
                WHERE client_id = ?
            ''', (now.isoformat(), client_id))
            conn.commit()
            conn.close()
            print(f"✅ Marked {client_id} as paid")
            return
        
        # PostgreSQL
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE user_usage 
            SET is_paid = TRUE, updated_at = %s
            WHERE client_id = %s
        ''', (now, client_id))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Marked {client_id} as paid")
    
    def get_or_create_user(self, client_id):
        """Get user or create if doesn't exist."""
        user = self.get_user(client_id)
        if not user:
            self.create_user(client_id)
            user = self.get_user(client_id)
        return user
