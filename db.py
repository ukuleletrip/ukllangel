#! /usr/bin/env python
# -*- coding:utf-8 -*-
from google.appengine.ext import ndb

class User(ndb.Model):
    version = ndb.IntegerProperty(required=True, default=1)
    mid = ndb.StringProperty(required=True)
    worst_drinking = ndb.StringProperty()
    

class Watch(ndb.Model):
    version = ndb.IntegerProperty(required=True, default=1)
    date = ndb.DateTimeProperty(required=True)
    sent_count = ndb.IntegerProperty(required=True, default=0)
    is_replied = ndb.BooleanProperty(required=True, default=False)
    reply = ndb.StringProperty()


class Drinking(ndb.Model):
    version = ndb.IntegerProperty(required=True, default=1)
    mid = ndb.StringProperty(required=True)
    start_date = ndb.DateTimeProperty(required=True)
    is_done = ndb.BooleanProperty(required=True, default=False)
    result = ndb.StringProperty()
    watches = ndb.StructuredProperty(Watch, repeated=True)

    @staticmethod
    def get_key(key):
        return ndb.Key(Drinking, key)

    @staticmethod
    def delete_drinkings(drinkings):
        ndb.delete_multi(drinkings)
