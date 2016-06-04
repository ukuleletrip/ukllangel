#! /usr/bin/env python
# -*- coding:utf-8 -*-
#
# usage: python -m unittest mylinebot_test.MyLineBotTestCase.testParseDate
#        ./mylinebot_test.py
#

import sys, os
sys.path.insert(0, 'libs')
sys.path.insert(1, '/usr/local/google_appengine')
sys.path.insert(1, '/usr/local/google_appengine/lib/yaml/lib')
from bs4 import BeautifulSoup

import unittest
from google.appengine.ext import ndb
from google.appengine.ext import testbed

from mylinebot import *
from db import Drinking, Watch, User
from datetime import datetime, timedelta
from tzimpl import JST, UTC
from time import sleep

tz_jst = JST()
tz_utc = UTC()

class MyLineBotTestCase(unittest.TestCase):
    def setUp(self):
        # First, create an instance of the Testbed class.
        self.testbed = testbed.Testbed()
        # Then activate the testbed, which prepares the service stubs for use.
        self.testbed.activate()
        # Next, declare which service stubs you want to use.
        self.testbed.init_urlfetch_stub()
        self.testbed.init_datastore_v3_stub()
        self.testbed.init_memcache_stub()
        # Clear ndb's in-context cache between tests.
        # This prevents data from leaking between tests.
        # Alternatively, you could disable caching by
        # using ndb.get_context().set_cache_policy(False)
        ndb.get_context().clear_cache()


    def tearDown(self):
        self.testbed.deactivate()

        
    def testParseDate(self):
        test_patterns = []
        now = datetime.now()+timedelta(minutes=5)

        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%d時%d分から飲む' % (now.hour, now.minute),
                               'msgpm' : u'午後%d時%d分から呑む' % (now.hour%12, now.minute)})
        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%02d時%02d分からのむ' % (now.hour, now.minute),
                               'msgpm' : u'PM%02d時%02d分から飲む' % (now.hour%12, now.minute)})
        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%02d%02dから呑む' % (now.hour, now.minute),
                               'msgpm' : u'%02d%02dPMからのむ' % (now.hour%12, now.minute)})
        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%02d%02dから飲み会' % (now.hour, now.minute),
                               'msgpm' : u'%02d%02dPMから呑み会' % (now.hour%12, now.minute)})
        now2 = (now+timedelta(hours=1)).replace(minute=0)
        test_patterns.append({ 'date'  : now2,
                               'msg'   : u'%d時から飲む' % (now2.hour),
                               'msgpm' : u'pm%d時から飲む' % (now2.hour%12)})
        test_patterns.append({ 'date'  : now2,
                               'msg'   : u'%dじから飲む' % (now2.hour),
                               'msgpm' : u'ごご%dじから飲む' % (now2.hour%12)})

        for test in test_patterns:
            dt = test['date']

            msg = handle_message('test', test['msg'])
            if msg is None:
                msg = u''
            c_msg = u'%d月%d日%d時%d分から飲むのですね' % (dt.month, dt.day, dt.hour, dt.minute)
            self.assertTrue(msg.startswith(c_msg), msg + '\n' + c_msg)
            cancel_drinking('test')

            msg = handle_message('test', test['msgpm'])
            if msg is None:
                msg = u''
            c_msg = u'%d月%d日%d時%d分から飲むのですね' % \
                    (dt.month, dt.day, dt.hour if dt.hour >=12 else dt.hour+12, dt.minute)
            self.assertTrue(msg.startswith(c_msg), msg + '\n' + c_msg)
            cancel_drinking('test')


    def testNow(self):
        test_id = 'test'
        dt = datetime.now()
        c_msg = u'%d月%d日%d時' % (dt.month, dt.day, dt.hour)
        msg = handle_message(test_id, u'今から飲む')
        self.assertTrue(msg.startswith(c_msg), msg)
                

    def testPastDrinking(self):
        now = datetime.now()+timedelta(minutes=-5)
        msg = u'%02d%02dから飲む' % (now.hour, now.minute)
        msg = handle_message('test', msg)
        self.assertTrue(msg.find(u'は過去です')>=0, msg)


    def testDuplicatedDrinking(self):
        now = datetime.now()+timedelta(minutes=5)
        msg = u'%02d%02dから飲む' % (now.hour, now.minute)
        handle_message('test', msg)
        msg = handle_message('test', msg)
        self.assertTrue(msg.find(u'飲みは1つしか予約できません')>=0, msg)


    def testWatch(self):
        TEST_INTERVAL = 10
        test_id = 'test'
        watches = []
        now_utc = utc_now()+timedelta(seconds=TEST_INTERVAL)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL+2)

        for i in range(WATCH_COUNTS):
            sleep(TEST_INTERVAL)

            watch_drinkings()
            (status, info) = get_status(test_id, is_peek=True)
            self.assertEqual(status, User.STAT_WAIT_REPLY)
            self.assertTrue(info is not None)
            self.assertEqual(info['key'], test_id, info['key'])
            self.assertEqual(info['idx'], i, info['idx'])
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, True if j < i else False)
                self.assertEqual(watch.sent_count, 1 if j <= i else 0)

            self.assertFalse(drinking.is_done)
            content = { 'from' : test_id, 'text' : 'OK' }
            receive_message(content)
            (status, info) = get_status(test_id)
            self.assertEqual(status, User.STAT_NONE)
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, True if j <= i else False)
                self.assertEqual(watch.sent_count, 1 if j <= i else 0)

        drinking = Drinking.get_key(test_id).get()
        self.assertFalse(drinking.is_done)


    def testWatchDelayed(self):
        TEST_INTERVAL = 5
        test_id = 'test'
        watches = []
        now_utc = utc_now()+timedelta(seconds=TEST_INTERVAL)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL*2+2)

        for i in range(5):
            watch_drinkings()
            (status, info) = get_status(test_id, True)
            self.assertEqual(status, User.STAT_WAIT_REPLY)
            self.assertTrue(info is not None)
            self.assertEqual(info['key'], test_id, info['key'])
            self.assertEqual(info['idx'], 0)
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, False)
                self.assertEqual(watch.sent_count, i+1 if j == 0 else 0)

        drinking = Drinking.get_key(test_id).get()
        self.assertFalse(drinking.is_done)


    def testWatchFinished(self):
        TEST_INTERVAL = 5
        test_id = 'test'
        watches = []
        now_utc = utc_now()+timedelta(seconds=TEST_INTERVAL)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL*2+2)

        watch_drinkings()
        (stat, info) = get_status(test_id, is_peek=True)
        msg = handle_reply(test_id, u'帰宅した', info)
        self.assertTrue(msg.startswith(u'お疲れさまでした'), msg)

        drinking = Drinking.get_key(test_id).get()
        self.assertEqual(drinking.finished_date.year, now_utc.year)
        self.assertEqual(drinking.finished_date.month, now_utc.month)
        self.assertEqual(drinking.finished_date.day, now_utc.day)
        self.assertEqual(drinking.finished_date.hour, now_utc.hour)
        for j, watch in enumerate(drinking.watches):
            self.assertEqual(watch.is_replied, True if j == 0 else False)
            self.assertEqual(watch.sent_count, 1 if j == 0 else 0)
        self.assertTrue(drinking.is_done)


    def testResult(self):
        test_id = 'test'
        watches = []
        now_utc = utc_now()+timedelta(days=-1)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=WATCH_INTERVAL*(i+1)),
                                 sent_count=1,
                                 is_replied=True))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            is_done=True,
                            watches=watches)
        drinking.put()

        check_result()
        (status, info) = get_status(test_id)
        self.assertEqual(status, User.STAT_WAIT_RESULT)

        result = u'二日酔い'
        msg = handle_result(test_id, result, info)

        drinking = Drinking.get_key(test_id).get()
        self.assertEqual(drinking.result, result, drinking.result)
        self.assertTrue(msg.find(u'次回も大人飲み') >= 0, msg)


    def testCancel(self):
        test_id = 'test'
        now = datetime.now()+timedelta(minutes=5)
        handle_message(test_id, u'%02d%02dから呑む' % (now.hour, now.minute))
        msg = handle_message(test_id, u'やめ')
        if msg is None:
            msg = u''
        self.assertEqual(msg, u'予約された飲みをキャンセルしました', msg)


    def testFinish(self):
        test_id = 'test'
        now = datetime.now()+timedelta(minutes=5)
        handle_message(test_id, u'%02d%02dから呑む' % (now.hour, now.minute))
        msg = handle_message(test_id, u'帰宅した')
        if msg is None:
            msg = u''
        self.assertTrue(msg.startswith(u'お疲れさま'), msg)

    def testMultiFinish(self):
        test_id = 'test'
        now = datetime.now()+timedelta(minutes=5)
        handle_message(test_id, u'%02d%02dから呑む' % (now.hour, now.minute))
        msg = handle_message(test_id, u'帰宅した')
        if msg is None:
            msg = u''
        self.assertTrue(msg.startswith(u'お疲れさま'), msg)
        msg = handle_message(test_id, u'帰宅した')
        self.assertTrue(msg.startswith(u'すでに帰宅'), msg)

    def testHistory(self):
        test_id = 'test'
        test_msgs = [ u'過去の飲みは？',
                      u'これまでの呑みを',
                      u'今までの呑みは？',
                      u'前の呑み'
        ]
        for test_msg in test_msgs:
            msg = handle_message(test_id, test_msg)
            self.assertTrue(msg.startswith(u'まだ飲みの'), msg)

        user = User(id=test_id)
        user.put()
        for test_msg in test_msgs:
            msg = handle_message(test_id, test_msg)
            self.assertTrue(msg.startswith(u'過去の飲みは'), msg)

        user = User.get_key(test_id).get()
        self.assertTrue(user.history_url != None and len(user.history_url) > 0)
        self.assertTrue(user.history_expire < utc_now()+timedelta(minutes=HISTORY_DURATION))

        sdt = utc_now()+timedelta(days=-2)
        check_sdt = []
        for i in range(MAX_HISTORY+1):
            watches = []
            for j in range(WATCH_COUNTS):
                watches.append(Watch(date=sdt+timedelta(minutes=WATCH_INTERVAL*(i+1))))

            key = test_id + sdt.strftime('%Y%m%d%H%M')
            drinking = Drinking(id=key,
                                mid=test_id,
                                start_date=sdt,
                                watches=watches)
            drinking.put()
            check_sdt.append(format_jdate(sdt.replace(tzinfo=tz_utc).astimezone(tz_jst)))
            sdt = sdt+timedelta(days=-1)

            
        self.assertEqual(get_drinking_history_content(user.history_url[:-1]), None)
                         
        soup = BeautifulSoup(get_drinking_history_content(user.history_url), 'html.parser')
        trs = soup.body.div.table.tbody.findAll('tr')
        self.assertEqual(len(trs), MAX_HISTORY*2)
        for i in range(MAX_HISTORY):
            self.assertEqual(trs[i*2].td.text, check_sdt[i])

        
if __name__ == '__main__':
    unittest.main()
