#!/usr/bin/env python
# -*- coding:utf-8 -*-

import jinja2
import logging
import os
import urllib
import webapp2

from functools import wraps

from google.appengine.api import images
from google.appengine.api import mail
from google.appengine.api import memcache
# from google.appengine.api import urlfetch
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers
from google.appengine.runtime import apiproxy_errors
from google.appengine.ext import ndb

from webapp2_extras import sessions
from webapp2_extras import sessions_memcache

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')
JINJA_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
                               autoescape=True)


# Webapp2 Sessions config
config = {}
config['webapp2_extras.sessions'] = {
    'secret_key': 'carolinas-not-so-secret-key',
    'name': 'navette_session',
}


def rate_limit(seconds_per_request=1):
    def rate_limiter(function):
        @wraps(function)
        def wrapper(self, *args, **kwargs):
            added = memcache.add('%s:%s' %
                                 (self.__class__.__name__,
                                  self.request.remote_addr or ''), 1,
                                 time=seconds_per_request,
                                 namespace='rate_limiting')
            if not added:
                self.response.write('Rate limit exceeded')
                self.response.set_status(403)
                return

            return function
        return wrapper
    return rate_limiter


def parseAcceptLanguage(acceptLanguage):
    languages = acceptLanguage.split(",")
    locale_q_pairs = []

    for language in languages:
        if language.split(";")[0] == language:
            # no q => q = 1
            locale_q_pairs.append((language.strip(), "1"))
        else:
            locale = language.split(";")[0].strip()
            q = language.split(";")[1].split("=")[1]
            locale_q_pairs.append((locale, q))

    return locale_q_pairs


def detectLocale(acceptLanguage):
    defaultLocale = 'en'
    supportedLocales = ['no', 'en']

    locale_q_pairs = parseAcceptLanguage(acceptLanguage)
    for pair in locale_q_pairs:
        for locale in supportedLocales:
            # pair[0] is locale, pair[1] is q value
            if pair[0].replace('-', '_').lower().startswith(locale.lower()):
                return locale

    return defaultLocale


class Handler(webapp2.RequestHandler):
    def write(self, *a, **kw):
        self.response.out.write(*a, **kw)

    @classmethod
    def render_str(cls, template, *a, **params):
        template = JINJA_ENV.get_template(template)
        return template.render(params)

    def render(self, template, *a, **params):
        self.write(self.render_str(template,
                                   *a, **params))

    def dispatch(self):
        self.session_store = sessions.get_store(request=self.request)
        try:
            webapp2.RequestHandler.dispatch(self)
        finally:
            self.session_store.save_sessions(self.response)

    @webapp2.cached_property
    def session(self):
        return self.session_store.get_session(name='navette_session',
                                              factory=sessions_memcache.
                                              MemcacheSessionFactory)


class ChangeLocale(Handler):
    def get(self, locale):
        if locale == 'en':
            self.session['locale'] = locale
        elif locale == 'no':
            self.session['locale'] = locale
        else:
            self.redirect(self.request.referer)
        self.redirect(self.request.referer)


class MainHandler(Handler):
    def get(self):
        self.render('darbs.html')


class ServicesHandler(Handler):
    def get(self):
        self.render("darbs.html")


class ToursHandler(Handler):
    def get(self):
        events = Event.query().order(Event.added)
        self.render("atputa.html", events=events)


class ContactsHandler(Handler):
    def get(self):
        self.render("kontakti.html")


class AboutHandler(Handler):
    def get(self):
        self.render("par.html")


class TourHandler(Handler):
    def get(self, id_str):
        event = None
        # if key_str:
        #     key = ndb.Key(urlsafe=key_str)
        #     if key:
        #         event = key.get()
        if id_str:
            event = Event.get_by_id(int(id_str))
        self.render("brauciens.html", event=event)


class MailHandler(Handler):
    @rate_limit(seconds_per_request=15)
    def post(self):
        user_info = self.request.get('contact')
        message = self.request.get('msg')
        if user_info:
            message += " - %s" % user_info
        from_addr = "info@saldusgaisma.lv"
        to_addr = "nejeega@gmail.com"

        try:
            msg = mail.EmailMessage()
            msg.sender = from_addr
            msg.to = to_addr
            msg.subject = "No maajas lapas"
            msg.html = message
            msg.send()
            self.redirect(self.request.referer)
        except apiproxy_errors.OverQuotaError, message:
            logging.error(message)


class Event(ndb.Model):
    eventName = ndb.TextProperty(required=True)
    startDate = ndb.TextProperty(required=True)
    endDate = ndb.TextProperty()
    location = ndb.TextProperty(required=True)
    excerpt = ndb.TextProperty(required=True)
    description = ndb.TextProperty(required=True)
    price = ndb.IntegerProperty(required=True)
    multipriced = ndb.TextProperty()
    image = ndb.BlobProperty()
    added = ndb.DateTimeProperty(auto_now_add=True)

    @staticmethod
    def addEvent(kind, **params):
        event = Event(**params)
        if event:
            event.put()
        return event


class EventHandler(Handler, blobstore_handlers.BlobstoreUploadHandler):
    def get(self):
        upload_url = blobstore.create_upload_url('/atputa/pievienot')
        self.render("pievienot.html", ul_url=str(upload_url))

    def post(self):
        en = self.request.get('eventName')
        sd = self.request.get('startDate')
        ed = self.request.get('endDate')
        ex = self.request.get('excerpt')
        de = self.request.get('description')
        loc = self.request.get('location')
        pr = int(self.request.get('price'))
        mp = self.request.get("multipriced")

        params = {'eventName': en, 'startDate': sd, 'location': loc,
                  'excerpt': ex, 'description': de, 'price': pr}

        if ed:
            params['endDate'] = ed

        if mp:
            params['multipriced'] = mp

        # Get the picture and upload it
        upload_files = self.get_uploads('picture')
        # Get the key from blobstore for the first element
        if upload_files:
            blob_info = upload_files[0]
            img = blob_info.key()
            params['image'] = str(img)

        event = Event.addEvent('Event', **params)
        # self.redirect(self.request.referer)
        # self.redirect('/serve/' + str(img))
        self.redirect('/atputa/' + str(event.key.id()))



# Blobstore
class UploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    def post(self):
        # 'file' is file upload field in the form
        upload_files = self.get_uploads('file')
        blob_info = upload_files[0]
        self.redirect('/serve/%s' % blob_info.key())


class ServeHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        resource = str(urllib.unquote(resource))
        blob_info = blobstore.BlobInfo.get(resource)
        self.send_blob(blob_info)


class ThumbnailHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        resource = str(urllib.unquote(resource))
        blob_info = blobstore.BlobInfo.get(resource)
        width = self.request.get('w')
        height = self.request.get('h')
        if not width:
            width = 700
        else:
            width = int(width)
        if not height:
            height = 700
        else:
            height = int(height)

        if blob_info:
            img = images.Image(blob_key=resource)
            img.resize(width=width, height=height)
            thumbnail = img.execute_transforms(
                output_encoding=images.JPEG)

            self.response.headers['Content-Type'] = 'image/jpeg'
            self.response.out.write(thumbnail)
            return

        # Either blob_key wasnt provided or there was no value with that ID
        # in the Blobstore
        self.error(404)


class GetsbijHandler(Handler):
    def get(self):
        self.render("getsbijs.html")


class ZRLHandler(Handler):
    def get(self):
        self.render("zrl.html")


class SPAHandler(Handler):
    def get(self):
        self.render("spa.html")


app = webapp2.WSGIApplication([
    ('/upload', UploadHandler),
    ('/serve/([^/]+)?', ServeHandler),
    ('/serve/th/([^/]+)?', ThumbnailHandler),
    ('/pakalpojumi*', ServicesHandler),
    ('/atputa/pievienot', EventHandler),
    ('/atputa/(.*)?', TourHandler),
    ('/atputa.*', ToursHandler),
    ('/kontakti*', ContactsHandler),
    ('/mail', MailHandler),
    ('/.*', MainHandler)
], config=config, debug=True)
