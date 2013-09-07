# -*- coding: utf-8 -*-
"""
Description: database models

Copyright (c) 2013— Andrea Peltrin
Portions are copyright (c) 2013 Rui Carmo
License: MIT (see LICENSE.md for details)
"""

import pickle
from datetime import datetime, timedelta
from peewee import *

from utilities import *
import favicon
from coldsweat import config, log

# Defer database init, see connect() below
engine = config.get('database', 'engine')
if engine == 'sqlite':
    from sqlite3 import IntegrityError, ProgrammingError
    _db = SqliteDatabase(None, threadlocals=True) 
elif engine == 'mysql':
    from MySQLdb import IntegrityError, ProgrammingError
    _db = MySQLDatabase(None)
else:
    raise ValueError('Unknown database engine %s. Should be sqlite or mysql' % engine)

# ------------------------------------------------------
# Custom fields
# ------------------------------------------------------

class PickleField(BlobField):
    def db_value(self, value):
        return super(PickleField, self).db_value(pickle.dumps(value, 2)) # Use newer protocol 

    def python_value(self, value):
        return pickle.loads(value)

# ------------------------------------------------------
# Coldsweat models
# ------------------------------------------------------

class CustomModel(Model):
    """
    Binds the database to all our models
    """

    class Meta:
        database = _db


class User(CustomModel):
    """
    Users - need at least one to store the api_key
    """    
    DEFAULT_CREDENTIALS = 'coldsweat', 'coldsweat'

    username            = CharField(unique=True)
    password            = CharField() #@@TODO: hashed & salted    
    email               = CharField(null=True)
    api_key             = CharField(unique=True)
    is_enabled          = BooleanField(default=True) 

    class Meta:
        db_table = 'users'
    
    @staticmethod
    def make_api_key(username, password):
        return make_md5_hash('%s:%s' % (username, password))
    

class Icon(CustomModel):
    """
    Feed (fav)icons, stored as data URIs
    """
    data                = TextField() 

    class Meta:
        db_table = 'icons'


class Group(CustomModel):
    """
    Feed group/folder
    """
    DEFAULT_GROUP = 'All entries'
    
    title               = CharField(null=True)
    
    class Meta:  
        db_table = 'groups'    


class Feed(CustomModel):
    """
    Atom/RSS feed
    """
    
    # Fetch?
    is_enabled          = BooleanField(default=True)     
    # A URL to a small icon representing the feed
    icon                = ForeignKeyField(Icon, default=1)
    title               = CharField(null=True, default='Untitled')        
    # The URL of the HTML page associated with the feed
    alternate_link      = CharField(null=True)            
    # The URL of the feed itself (rel=self)
    self_link           = CharField()    
    etag                = CharField(null=True)    
    last_updated_on     = DateTimeField(null=True) # As UTC
    last_checked_on     = DateTimeField(null=True) # As UTC 
    last_status         = IntegerField(null=True) # Last HTTP code    
    error_count         = IntegerField(default=0)

    class Meta:
        indexes = (
            (('self_link',), True),
            (('last_checked_on',), False),
            #(('last_updated_on',), False),
        )
        #order_by = ('-last_updated_on',)
        db_table = 'feeds'

    @property
    def last_updated_on_as_epoch(self):
        if self.last_updated_on: # Never updated?
            return datetime_as_epoch(self.last_updated_on)
        return 0 


class Entry(CustomModel):
    """
    Atom/RSS entry
    """

    # It's called 'id' in Atom parlance
    guid            = CharField()     
    feed            = ForeignKeyField(Feed,related_name='entries')    
    title           = CharField(default='Untitled')
    author          = CharField(null=True)
    content         = TextField(null=True)
    link            = CharField(null=True)    
    last_updated_on = DateTimeField() # As UTC

    class Meta:
        indexes = (
            #(('last_updated_on',), False),
            (('guid',), False),
            (('link',), False),
        )
        #order_by = ('-last_updated_on',)
        db_table = 'entries'

    @property
    def last_updated_on_as_epoch(self):
        return datetime_as_epoch(self.last_updated_on)

    @property
    def excerpt(self):
        return get_excerpt(self.content)

                
class Saved(CustomModel):
    """
    Many-to-many relationship between Users and entries
    """
    user           = ForeignKeyField(User)
    entry          = ForeignKeyField(Entry)    
    saved_on       = DateTimeField(default=datetime.utcnow)  

    class Meta:
        indexes = (
            (('user', 'entry'), True),
        )
        #db_table = 'saved'        


class Read(CustomModel):
    """
    Many-to-many relationship between Users and entries
    """
    user           = ForeignKeyField(User)
    entry          = ForeignKeyField(Entry)    
    read_on        = DateTimeField(default=datetime.utcnow) 

    class Meta:
        indexes = (
            (('user', 'entry'), True),
        )
        #db_table = 'read'


class Subscription(CustomModel):
    """
    A user's feed subscriptions
    """
    user           = ForeignKeyField(User)
    group          = ForeignKeyField(Group)
    feed           = ForeignKeyField(Feed)

    class Meta:
        indexes = (
            (('user', 'group', 'feed'), True),
        )    
        db_table = 'subscriptions'


class Session(CustomModel):
    
    key             = CharField(null=False)
    value           = PickleField(null=False)     
    expires         = DateTimeField(null=False)

    class Meta:
        indexes = (
            (('key', ), True),
        )  
        db_table = 'sessions' 


# ------------------------------------------------------
# Utility functions
# ------------------------------------------------------

def connect():
    """
    Shortcut to init and connect to database
    """
    
    if engine == 'sqlite':
        filename = config.get('database', 'filename')
        
        _db.init(filename)    
        
        # See http://www.sqlite.org/wal.html
        _db.execute_sql('PRAGMA journal_mode=WAL')
        
    elif engine == 'mysql':            
        database = config.get('database', 'database')

        kwargs = dict(
            host    = config.get('database', 'hostname'),
            user    = config.get('database', 'username'),
            passwd  = config.get('database', 'password')        
        )

        _db.init(database, **kwargs)
    
    _db.connect()


def transaction():
    return _db.transaction()

def close():
    try: 
        # Attempt to close database connection 
        _db.close()
    except ProgrammingError, exc:
        log.error('Caught exception while closing database connection: %s' % exc)


def setup(username, password):
    """
    Create database and tables for all models and setup bootstrap data
    """

    models = User, Icon, Feed, Entry, Group, Read, Saved, Subscription, Session

    for model in models:
        model.create_table(fail_silently=True)
        
    try:
        User.get(User.username == username)
        return # Already set up, bail out
    except User.DoesNotExist:
        pass

    # Create the bare minimum to boostrap system
    with _db.transaction():
        User.create(username=username, password=password, api_key=User.make_api_key(username, password))
        Group.create(title=Group.DEFAULT_GROUP)        
        Icon.create(data=favicon.DEFAULT_FAVICON)
