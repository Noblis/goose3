# -*- coding: utf-8 -*-
"""\
This is a python port of "Goose" orignialy licensed to Gravity.com
under one or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.

Python port was written by Xavier Grangier for Recrutae

Gravity.com licenses this file
to you under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import os
import glob
from copy import deepcopy

from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

import dateutil.parser
from dateutil.tz import tzutc

from goose3.article import Article
from goose3.utils import URLHelper, RawHelper
from goose3.text import get_encodings_from_content
from goose3.extractors.content import StandardContentExtractor
from goose3.extractors.videos import VideoExtractor
from goose3.extractors.title import TitleExtractor
from goose3.extractors.images import ImageExtractor
from goose3.extractors.links import LinksExtractor
from goose3.extractors.tweets import TweetsExtractor
from goose3.extractors.authors import AuthorsExtractor
from goose3.extractors.tags import TagsExtractor
from goose3.extractors.opengraph import OpenGraphExtractor
from goose3.extractors.publishdate import PublishDateExtractor, TIMEZONE_INFO
from goose3.extractors.schema import SchemaExtractor
from goose3.extractors.metas import MetasExtractor
from goose3.cleaners import StandardDocumentCleaner
from goose3.outputformatters import StandardOutputFormatter

from goose3.network import NetworkFetcher


class CrawlCandidate(object):
    def __init__(self, config, url, raw_html):
        self.config = config
        # parser
        self.parser = self.config.get_parser()
        self.url = url
        self.raw_html = raw_html


class Crawler(object):
    def __init__(self, config, fetcher=None):
        # config
        self.config = config
        # parser
        self.parser = self.config.get_parser()

        # article
        self.article = Article()

        # init the extractor
        self.extractor = self.get_extractor()

        # init the document cleaner
        self.cleaner = self.get_cleaner()

        # init the output formatter
        self.formatter = self.get_formatter()

        # metas extractor
        self.metas_extractor = self.get_metas_extractor()

        # opengraph extractor
        self.opengraph_extractor = self.get_opengraph_extractor()

        # schema.org news article extractor
        self.schema_extractor = self.get_schema_extractor()

        # publishdate extractor
        self.publishdate_extractor = self.get_publishdate_extractor()

        # tags extractor
        self.tags_extractor = self.get_tags_extractor()

        # authors extractor
        self.authors_extractor = self.get_authors_extractor()

        # tweets extractor
        self.tweets_extractor = self.get_tweets_extractor()

        # links extractor
        self.links_extractor = self.get_links_extractor()

        # video extractor
        self.video_extractor = self.get_video_extractor()

        # title extractor
        self.title_extractor = self.get_title_extractor()

        # html fetcher
        if isinstance(fetcher, NetworkFetcher):
            self.fetcher = fetcher
        else:
            self.fetcher = NetworkFetcher(self.config)

        # image extractor
        self.image_extractor = self.get_image_extractor()

        # TODO: use the log prefix
        self.log_prefix = "crawler: "

    def crawl(self, crawl_candidate):

        # parser candidate
        parse_candidate = self.get_parse_candidate(crawl_candidate)

        # raw html
        raw_html = self.get_html(crawl_candidate, parse_candidate)

        if raw_html is None:
            return self.article

        return self.process(raw_html, parse_candidate.url, parse_candidate.link_hash)

    def process(self, raw_html, final_url, link_hash):

        # create document
        doc = self.get_document(raw_html)

        # article
        self.article._final_url = final_url
        self.article._link_hash = link_hash
        self.article._raw_html = raw_html
        self.article._doc = doc
        self.article._raw_doc = deepcopy(doc)

        # open graph
        self.article._opengraph = self.opengraph_extractor.extract()

        # schema.org:
        #  - (ReportageNewsArticle) https://pending.schema.org/ReportageNewsArticle
        #  - (NewsArticle) https://schema.org/NewsArticle
        #  - (Article) https://schema.org/Article
        self.article._schema = self.schema_extractor.extract()

        if not self.article._final_url:
            if "url" in self.article.opengraph:
                self.article._final_url = self.article.opengraph["url"]
            elif self.article.schema and "url" in self.article.schema:
                self.article._final_url = self.article.schema["url"]

        # meta
        metas = self.metas_extractor.extract()
        # print(metas)
        self.article._meta_lang = metas['lang']
        self.article._meta_favicon = metas['favicon']
        self.article._meta_description = metas['description']
        self.article._meta_keywords = metas['keywords']
        self.article._meta_encoding = metas['encoding']
        self.article._canonical_link = metas['canonical']
        self.article._domain = metas['domain']

        # publishdate
        self.article._publish_date = self.publishdate_extractor.extract()
        if self.article.publish_date:
            try:
                publish_datetime = dateutil.parser.parse(self.article.publish_date, tzinfos=TIMEZONE_INFO)
                if publish_datetime.tzinfo:
                    self.article._publish_datetime_utc = publish_datetime.astimezone(tzutc())
                else:
                    self.article._publish_datetime_utc = publish_datetime
            except (ValueError, OverflowError):
                self.article._publish_datetime_utc = None

        # tags
        self.article._tags = self.tags_extractor.extract()

        # authors
        self.article._authors = self.authors_extractor.extract()

        # title
        self.article._title = self.title_extractor.extract()

        # jump through some hoops on attempting to get a language if not found
        if self.article._meta_lang is None:
            tmp_lang_detect = "{} {} {} {}".format(self.article._meta_description, self.article._title, self.article._meta_keywords, self.article._tags)
            tmp_lang_detect = " ".join(tmp_lang_detect.split())
            if len(tmp_lang_detect) > 15:
                # required to make it deterministic;
                # see: https://github.com/Mimino666/langdetect/blob/master/README.md#basic-usage
                DetectorFactory.seed = 0
                try:
                    self.article._meta_lang = detect(tmp_lang_detect)
                except LangDetectException:
                    self.article._meta_lang = None
            # print(self.article._meta_lang)

        # check for known node as content body
        # if we find one force the article.doc to be the found node
        # this will prevent the cleaner to remove unwanted text content
        article_body = self.extractor.get_known_article_tags()
        if article_body is not None:
            doc = article_body

        # before we do any calcs on the body itself let's clean up the document
        if not isinstance(doc, list):
            doc = [self.cleaner.clean(doc)]
        else:
            doc = [self.cleaner.clean(deepcopy(x)) for x in doc]

        # big stuff
        self.article._top_node = self.extractor.calculate_best_node(doc)

        # if we do not find an article within the discovered possible article nodes,
        # try again with the root node.
        if self.article._top_node is None:
            # try again with the root node.
            self.article._top_node = self.extractor.calculate_best_node(self.article._doc)
        else:
            # set the doc member to the discovered article node.
            self.article._doc = doc

        # if we have a top node
        # let's process it
        if self.article._top_node is not None:

            # article links
            self.article._links = self.links_extractor.extract()

            # tweets
            self.article._tweets = self.tweets_extractor.extract()

            # video handling
            self.article._movies = self.video_extractor.get_videos()

            # image handling
            if self.config.enable_image_fetching:
                self.get_image()

            # post cleanup
            self.article._top_node = self.extractor.post_cleanup()

            # clean_text
            self.article._cleaned_text = self.formatter.get_formatted_text()

        # cleanup tmp file
        self.release_resources()

        # return the article
        return self.article

    @staticmethod
    def get_parse_candidate(crawl_candidate):
        if crawl_candidate.raw_html:
            return RawHelper.get_parsing_candidate(crawl_candidate.url, crawl_candidate.raw_html)
        return URLHelper.get_parsing_candidate(crawl_candidate.url)

    def get_image(self):
        doc = self.article.raw_doc
        top_node = self.article.top_node
        self.article._top_image = self.image_extractor.get_best_image(doc, top_node)

    def get_html(self, crawl_candidate, parsing_candidate):
        # we got a raw_tml
        # no need to fetch remote content
        if crawl_candidate.raw_html:
            return crawl_candidate.raw_html

        # fetch HTML
        response = self.fetcher.fetch_obj(parsing_candidate.url)
        if response.encoding != 'ISO-8859-1':  # requests has a good idea; use what it says
            # return response as a unicode string
            html = response.text
            self.article._meta_encoding = response.encoding
        else:
            html = response.content
            encodings = get_encodings_from_content(response.text)
            if len(encodings) > 0:
                self.article._meta_encoding = encodings[0]
                response.encoding = encodings[0]
                html = response.text
            else:
                self.article._meta_encoding = encodings
        return html

    def get_metas_extractor(self):
        return MetasExtractor(self.config, self.article)

    def get_publishdate_extractor(self):
        return PublishDateExtractor(self.config, self.article)

    def get_opengraph_extractor(self):
        return OpenGraphExtractor(self.config, self.article)

    def get_schema_extractor(self):
        return SchemaExtractor(self.config, self.article)

    def get_tags_extractor(self):
        return TagsExtractor(self.config, self.article)

    def get_authors_extractor(self):
        return AuthorsExtractor(self.config, self.article)

    def get_tweets_extractor(self):
        return TweetsExtractor(self.config, self.article)

    def get_links_extractor(self):
        return LinksExtractor(self.config, self.article)

    def get_title_extractor(self):
        return TitleExtractor(self.config, self.article)

    def get_image_extractor(self):
        return ImageExtractor(self.fetcher, self.config, self.article)

    def get_video_extractor(self):
        return VideoExtractor(self.config, self.article)

    def get_formatter(self):
        return StandardOutputFormatter(self.config, self.article)

    def get_cleaner(self):
        return StandardDocumentCleaner(self.config, self.article)

    def get_document(self, raw_html):
        doc = self.parser.fromstring(raw_html)
        return doc

    def get_extractor(self):
        return StandardContentExtractor(self.config, self.article)

    def release_resources(self):
        path = os.path.join(self.config.local_storage_path, '%s_*' % self.article.link_hash)
        for fname in glob.glob(path):
            try:
                os.remove(fname)
            except OSError:
                # TODO: better log handeling
                pass
