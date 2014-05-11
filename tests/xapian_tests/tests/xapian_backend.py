import datetime
import sys
import xapian
import subprocess
import os

from django.conf import settings
from django.db import models
from django.test import TestCase

from haystack import connections, reset_search_queries
from haystack import indexes
from haystack.backends.xapian_backend import _marshal_value
from haystack.models import SearchResult
from haystack.query import SearchQuerySet, SQ
from haystack.utils.loading import UnifiedIndex

from core.models import MockTag, MockModel, AnotherMockModel, AFourthMockModel
from core.tests.mocks import MockSearchResult


def get_terms(backend, *args):
    result = subprocess.check_output(['delve'] + list(args) + [backend.path], env=os.environ.copy())
    result = result.split(": ")[1].strip()
    return result.split(" ")


class XapianMockModel(models.Model):
    """
    Same as tests.core.MockModel with a few extra fields for testing various
    sorting and ordering criteria.
    """
    author = models.CharField(max_length=255)
    foo = models.CharField(max_length=255, blank=True)
    pub_date = models.DateTimeField(default=datetime.datetime.now)
    exp_date = models.DateTimeField(default=datetime.datetime.now)
    tag = models.ForeignKey(MockTag)

    value = models.IntegerField(default=0)
    flag = models.BooleanField(default=True)
    slug = models.SlugField()
    popularity = models.FloatField(default=0.0)
    url = models.URLField()

    def __unicode__(self):
        return self.author


class XapianMockSearchIndex(indexes.SearchIndex):
    text = indexes.CharField(
        document=True, use_template=True,
        template_name='search/indexes/core/mockmodel_text.txt'
    )
    name = indexes.CharField(model_attr='author', faceted=True)
    pub_date = indexes.DateField(model_attr='pub_date')
    exp_date = indexes.DateField(model_attr='exp_date')
    value = indexes.IntegerField(model_attr='value')
    flag = indexes.BooleanField(model_attr='flag')
    slug = indexes.CharField(indexed=False, model_attr='slug')
    popularity = indexes.FloatField(model_attr='popularity')
    month = indexes.CharField(indexed=False)
    url = indexes.CharField(model_attr='url')
    empty = indexes.CharField()

    # Various MultiValueFields
    sites = indexes.MultiValueField()
    tags = indexes.MultiValueField()
    keys = indexes.MultiValueField()
    titles = indexes.MultiValueField()

    def get_model(self):
        return XapianMockModel

    def prepare_sites(self, obj):
        return ['%d' % (i * obj.id) for i in xrange(1, 4)]

    def prepare_tags(self, obj):
        if obj.id == 1:
            return ['a', 'b', 'c']
        elif obj.id == 2:
            return ['ab', 'bc', 'cd']
        else:
            return ['an', 'to', 'or']

    def prepare_keys(self, obj):
        return [i * obj.id for i in xrange(1, 4)]

    def prepare_titles(self, obj):
        if obj.id == 1:
            return ['object one title one', 'object one title two']
        elif obj.id == 2:
            return ['object two title one', 'object two title two']
        else:
            return ['object three title one', 'object three title two']

    def prepare_month(self, obj):
        return '%02d' % obj.pub_date.month

    def prepare_empty(self, obj):
        return ''


class XapianBoostMockSearchIndex(indexes.SearchIndex):
    text = indexes.CharField(
        document=True, use_template=True,
        template_name='search/indexes/core/mockmodel_template.txt'
    )
    author = indexes.CharField(model_attr='author', weight=2.0)
    editor = indexes.CharField(model_attr='editor')
    pub_date = indexes.DateField(model_attr='pub_date')

    def get_model(self):
        return AFourthMockModel


class XapianSimpleMockIndex(indexes.SearchIndex):
    text = indexes.CharField(document=True)
    author = indexes.CharField(model_attr='author')
    pub_date = indexes.DateTimeField(model_attr='pub_date')

    def get_model(self):
        return MockModel

    def prepare_text(self, obj):
        return 'this_is_a_word'


class XapianBackendTestCase(TestCase):
    def setUp(self):
        super(XapianBackendTestCase, self).setUp()

        self.old_ui = connections['default'].get_unified_index()
        self.ui = UnifiedIndex()
        self.index = XapianSimpleMockIndex()
        self.ui.build(indexes=[self.index])
        self.backend = connections['default'].get_backend()
        connections['default']._index = self.ui

        self.sample_objs = []

        mock = XapianMockModel()
        mock.id = 1
        mock.author = 'david'
        mock.pub_date = datetime.date(2009, 2, 25)

        self.backend.update(self.index, [mock])

    def test_fields(self):
        """
        Tests that all fields are in the database
        """
        terms = get_terms(self.backend, '-a')

        for field in ['id', 'author', 'pub_date', 'text']:
            is_inside = False
            for term in terms:
                if "X%s" % field.upper() in term:
                    is_inside = True
                    break
            self.assertTrue(is_inside, field)

    def test_text(self):
        terms = get_terms(self.backend, '-a')

        self.assertTrue('this_is_a_word' in terms)
        self.assertTrue('Zthis_is_a_word' in terms)

    def test_app_is_not_split(self):
        """
        Tests that the app path is not split
        and added as independent terms.
        """
        terms = get_terms(self.backend, '-a')

        self.assertFalse('tests' in terms)
        self.assertFalse('Ztest' in terms)

    def test_app_is_not_indexed(self):
        """
        Tests that the app path is not indexed.
        """
        terms = get_terms(self.backend, '-a')

        self.assertFalse('tests.xapianmockmodel.1' in terms)
        self.assertFalse('xapianmockmodel' in terms)
        self.assertFalse('tests' in terms)


class XapianSearchBackendTestCase(TestCase):
    def setUp(self):
        super(XapianSearchBackendTestCase, self).setUp()

        self.old_ui = connections['default'].get_unified_index()
        self.ui = UnifiedIndex()
        self.index = XapianMockSearchIndex()
        self.ui.build(indexes=[self.index])
        self.backend = connections['default'].get_backend()
        connections['default']._index = self.ui

        self.sample_objs = []

        for i in xrange(1, 4):
            mock = XapianMockModel()
            mock.id = i
            mock.author = 'david%s' % i
            mock.pub_date = datetime.date(2009, 2, 25) - datetime.timedelta(days=i)
            mock.exp_date = datetime.date(2009, 2, 23) + datetime.timedelta(days=i)
            mock.value = i * 5
            mock.flag = bool(i % 2)
            mock.slug = 'http://example.com/%d/' % i
            mock.url = 'http://example.com/%d/' % i
            self.sample_objs.append(mock)

        self.sample_objs[0].popularity = 834.0
        self.sample_objs[1].popularity = 35.5
        self.sample_objs[2].popularity = 972.0

        self.backend.update(self.index, self.sample_objs)

    def tearDown(self):
        self.backend.clear()
        connections['default']._index = self.old_ui
        super(XapianSearchBackendTestCase, self).tearDown()

    def test_update(self):
        self.assertEqual(self.backend.document_count(), 3)
        self.assertEqual([result.pk for result in self.backend.search(xapian.Query(''))['results']], [1, 2, 3])

    def test_duplicate_update(self):
        # Duplicates should be updated, not appended -- http://github.com/notanumber/xapian-haystack/issues/#issue/6
        self.backend.update(self.index, self.sample_objs)

        self.assertEqual(self.backend.document_count(), 3)

    def test_remove(self):
        self.backend.remove(self.sample_objs[0])
        self.assertEqual(self.backend.document_count(), 2)
        self.assertEqual([result.pk for result in self.backend.search(xapian.Query(''))['results']], [2, 3])

    def test_clear(self):
        self.backend.clear()
        self.assertEqual(self.backend.document_count(), 0)

        self.backend.update(self.index, self.sample_objs)
        self.assertEqual(self.backend.document_count(), 3)

        self.backend.clear([AnotherMockModel])
        self.assertEqual(self.backend.document_count(), 3)

        self.backend.clear([XapianMockModel])
        self.assertEqual(self.backend.document_count(), 0)

        self.backend.update(self.index, self.sample_objs)
        self.assertEqual(self.backend.document_count(), 3)

        self.backend.clear([AnotherMockModel, XapianMockModel])
        self.assertEqual(self.backend.document_count(), 0)

    def test_search(self):
        self.assertEqual(self.backend.search(xapian.Query()), {'hits': 0, 'results': []})
        self.assertEqual(self.backend.search(xapian.Query(''))['hits'], 3)
        self.assertEqual([result.pk for result in self.backend.search(xapian.Query(''))['results']], [1, 2, 3])
        self.assertEqual(self.backend.search(xapian.Query('indexed'))['hits'], 3)
        self.assertEqual([result.pk for result in self.backend.search(xapian.Query(''))['results']], [1, 2, 3])

        # Ensure that swapping the ``result_class`` works.
        self.assertTrue(isinstance(self.backend.search(xapian.Query('indexed'), result_class=MockSearchResult)['results'][0], MockSearchResult))

    def test_search_field_with_punctuation(self):
        # self.assertEqual(self.backend.search(xapian.Query('http://example.com/'))['hits'], 3)
        self.assertEqual([result.pk for result in self.backend.search(xapian.Query('http://example.com/1/'))['results']], [1])

    def test_search_by_mvf(self):
        self.assertEqual(self.backend.search(xapian.Query('ab'))['hits'], 1)
        self.assertEqual(self.backend.search(xapian.Query('b'))['hits'], 1)
        self.assertEqual(self.backend.search(xapian.Query('to'))['hits'], 1)
        self.assertEqual(self.backend.search(xapian.Query('one'))['hits'], 3)

    def test_field_facets(self):
        self.assertEqual(self.backend.search(xapian.Query(), facets=['name']), {'hits': 0, 'results': []})
        results = self.backend.search(xapian.Query('indexed'), facets=['name'])
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['fields']['name'], [('david1', 1), ('david2', 1), ('david3', 1)])

        results = self.backend.search(xapian.Query('indexed'), facets=['flag'])
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['fields']['flag'], [(False, 1), (True, 2)])

        results = self.backend.search(xapian.Query('indexed'), facets=['sites'])
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['fields']['sites'], [('1', 1), ('3', 2), ('2', 2), ('4', 1), ('6', 2), ('9', 1)])

    def test_date_facets(self):
        self.assertEqual(self.backend.search(xapian.Query(), date_facets={'pub_date': {'start_date': datetime.datetime(2008, 10, 26), 'end_date': datetime.datetime(2009, 3, 26), 'gap_by': 'month'}}), {'hits': 0, 'results': []})
        results = self.backend.search(xapian.Query('indexed'), date_facets={'pub_date': {'start_date': datetime.datetime(2008, 10, 26), 'end_date': datetime.datetime(2009, 3, 26), 'gap_by': 'month'}})
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['dates']['pub_date'], [
            ('2009-02-26T00:00:00', 0),
            ('2009-01-26T00:00:00', 3),
            ('2008-12-26T00:00:00', 0),
            ('2008-11-26T00:00:00', 0),
            ('2008-10-26T00:00:00', 0),
        ])

        results = self.backend.search(xapian.Query('indexed'), date_facets={'pub_date': {'start_date': datetime.datetime(2009, 02, 01), 'end_date': datetime.datetime(2009, 3, 15), 'gap_by': 'day', 'gap_amount': 15}})
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['dates']['pub_date'], [
            ('2009-03-03T00:00:00', 0),
            ('2009-02-16T00:00:00', 3),
            ('2009-02-01T00:00:00', 0)
        ])

    def test_query_facets(self):
        self.assertEqual(self.backend.search(xapian.Query(), query_facets={'name': 'da*'}), {'hits': 0, 'results': []})
        results = self.backend.search(xapian.Query('indexed'), query_facets={'name': 'da*'})
        self.assertEqual(results['hits'], 3)
        self.assertEqual(results['facets']['queries']['name'], ('da*', 3))

    def test_narrow_queries(self):
        self.assertEqual(self.backend.search(xapian.Query(), narrow_queries={'name:david1'}), {'hits': 0, 'results': []})
        results = self.backend.search(xapian.Query('indexed'), narrow_queries={'name:david1'})
        self.assertEqual(results['hits'], 1)

    def test_highlight(self):
        self.assertEqual(self.backend.search(xapian.Query(), highlight=True), {'hits': 0, 'results': []})
        self.assertEqual(self.backend.search(xapian.Query('indexed'), highlight=True)['hits'], 3)
        self.assertEqual([result.highlighted['text'] for result in self.backend.search(xapian.Query('indexed'), highlight=True)['results']], ['<em>indexed</em>!\n1', '<em>indexed</em>!\n2', '<em>indexed</em>!\n3'])

    def test_spelling_suggestion(self):
        self.assertEqual(self.backend.search(xapian.Query('indxe'))['hits'], 0)
        self.assertEqual(self.backend.search(xapian.Query('indxe'))['spelling_suggestion'], 'indexed')

        self.assertEqual(self.backend.search(xapian.Query('indxed'))['hits'], 0)
        self.assertEqual(self.backend.search(xapian.Query('indxed'))['spelling_suggestion'], 'indexed')

        self.assertEqual(self.backend.search(xapian.Query('foo'))['hits'], 0)
        self.assertEqual(self.backend.search(xapian.Query('foo'), spelling_query='indexy')['spelling_suggestion'], 'indexed')

        self.assertEqual(self.backend.search(xapian.Query('XNAMEdavid'))['hits'], 0)
        self.assertEqual(self.backend.search(xapian.Query('XNAMEdavid'))['spelling_suggestion'], 'david1')

    def test_more_like_this(self):
        results = self.backend.more_like_this(self.sample_objs[0])
        self.assertEqual(results['hits'], 2)
        self.assertEqual([result.pk for result in results['results']], [3, 2])

        results = self.backend.more_like_this(self.sample_objs[0], additional_query=xapian.Query('david3'))
        self.assertEqual(results['hits'], 1)
        self.assertEqual([result.pk for result in results['results']], [3])

        results = self.backend.more_like_this(self.sample_objs[0], limit_to_registered_models=True)
        self.assertEqual(results['hits'], 2)
        self.assertEqual([result.pk for result in results['results']], [3, 2])

        # Ensure that swapping the ``result_class`` works.
        self.assertTrue(isinstance(self.backend.more_like_this(self.sample_objs[0], result_class=MockSearchResult)['results'][0], MockSearchResult))

    def test_order_by(self):
        results = self.backend.search(xapian.Query(''), sort_by=['pub_date'])
        self.assertEqual([result.pk for result in results['results']], [3, 2, 1])

        results = self.backend.search(xapian.Query(''), sort_by=['-pub_date'])
        self.assertEqual([result.pk for result in results['results']], [1, 2, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['exp_date'])
        self.assertEqual([result.pk for result in results['results']], [1, 2, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['-exp_date'])
        self.assertEqual([result.pk for result in results['results']], [3, 2, 1])

        results = self.backend.search(xapian.Query(''), sort_by=['id'])
        self.assertEqual([result.pk for result in results['results']], [1, 2, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['-id'])
        self.assertEqual([result.pk for result in results['results']], [3, 2, 1])

        results = self.backend.search(xapian.Query(''), sort_by=['value'])
        self.assertEqual([result.pk for result in results['results']], [1, 2, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['-value'])
        self.assertEqual([result.pk for result in results['results']], [3, 2, 1])

        results = self.backend.search(xapian.Query(''), sort_by=['popularity'])
        self.assertEqual([result.pk for result in results['results']], [2, 1, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['-popularity'])
        self.assertEqual([result.pk for result in results['results']], [3, 1, 2])

        results = self.backend.search(xapian.Query(''), sort_by=['flag', 'id'])
        self.assertEqual([result.pk for result in results['results']], [2, 1, 3])

        results = self.backend.search(xapian.Query(''), sort_by=['flag', '-id'])
        self.assertEqual([result.pk for result in results['results']], [2, 3, 1])

    def test_verify_type(self):
        self.assertEqual([result.month for result in self.backend.search(xapian.Query(''))['results']], [u'02', u'02', u'02'])

    def test__marshal_value(self):
        self.assertEqual(_marshal_value('abc'), u'abc')
        self.assertEqual(_marshal_value(1), '000000000001')
        self.assertEqual(_marshal_value(2653), '000000002653')
        self.assertEqual(_marshal_value(25.5), '\xb2`')
        self.assertEqual(_marshal_value([1, 2, 3]), u'[1, 2, 3]')
        self.assertEqual(_marshal_value((1, 2, 3)), u'(1, 2, 3)')
        self.assertEqual(_marshal_value({'a': 1, 'c': 3, 'b': 2}), u"{'a': 1, 'c': 3, 'b': 2}")
        self.assertEqual(_marshal_value(datetime.datetime(2009, 5, 9, 16, 14)), u'20090509161400')
        self.assertEqual(_marshal_value(datetime.datetime(2009, 5, 9, 0, 0)), u'20090509000000')
        self.assertEqual(_marshal_value(datetime.datetime(1899, 5, 18, 0, 0)), u'18990518000000')
        self.assertEqual(_marshal_value(datetime.datetime(2009, 5, 18, 1, 16, 30, 250)), u'20090518011630000250')

    def test_build_schema(self):
        (content_field_name, fields) = self.backend.build_schema(connections['default'].get_unified_index().all_searchfields())
        self.assertEqual(content_field_name, 'text')
        self.assertEqual(len(fields), 15)
        self.assertEqual(fields, [
            {'column': 0, 'type': 'text', 'field_name': 'id', 'multi_valued': 'false'},
            {'column': 1, 'type': 'text', 'field_name': 'empty', 'multi_valued': 'false'},
            {'column': 2, 'type': 'date', 'field_name': 'exp_date', 'multi_valued': 'false'},
            {'column': 3, 'type': 'boolean', 'field_name': 'flag', 'multi_valued': 'false'},
            {'column': 4, 'type': 'text', 'field_name': 'keys', 'multi_valued': 'true'},
            {'column': 5, 'type': 'text', 'field_name': 'name', 'multi_valued': 'false'},
            {'column': 6, 'type': 'text', 'field_name': 'name_exact', 'multi_valued': 'false'},
            {'column': 7, 'type': 'float', 'field_name': 'popularity', 'multi_valued': 'false'},
            {'column': 8, 'type': 'date', 'field_name': 'pub_date', 'multi_valued': 'false'},
            {'column': 9, 'type': 'text', 'field_name': 'sites', 'multi_valued': 'true'},
            {'column': 10, 'type': 'text', 'field_name': 'tags', 'multi_valued': 'true'},
            {'column': 11, 'type': 'text', 'field_name': 'text', 'multi_valued': 'false'},
            {'column': 12, 'type': 'text', 'field_name': 'titles', 'multi_valued': 'true'},
            {'column': 13, 'type': 'text', 'field_name': 'url', 'multi_valued': 'false'},
            {'column': 14, 'type': 'long', 'field_name': 'value', 'multi_valued': 'false'}
        ])

    def test_parse_query(self):
        self.assertEqual(str(self.backend.parse_query('indexed')), 'Xapian::Query(Zindex:(pos=1))')
        self.assertEqual(str(self.backend.parse_query('name:david')), 'Xapian::Query(ZXNAMEdavid:(pos=1))')

        if xapian.minor_version() >= 2:
            self.assertEqual(str(self.backend.parse_query('name:da*')), 'Xapian::Query((XNAMEdavid1:(pos=1) SYNONYM XNAMEdavid2:(pos=1) SYNONYM XNAMEdavid3:(pos=1)))')
        else:
            self.assertEqual(str(self.backend.parse_query('name:da*')), 'Xapian::Query((XNAMEdavid1:(pos=1) OR XNAMEdavid2:(pos=1) OR XNAMEdavid3:(pos=1)))')

        self.assertEqual(str(self.backend.parse_query('name:david1..david2')), 'Xapian::Query(VALUE_RANGE 5 david1 david2)')
        self.assertEqual(str(self.backend.parse_query('value:0..10')), 'Xapian::Query(VALUE_RANGE 14 000000000000 000000000010)')
        self.assertEqual(str(self.backend.parse_query('value:..10')), 'Xapian::Query(VALUE_RANGE 14 %012d 000000000010)' % (-sys.maxint - 1))
        self.assertEqual(str(self.backend.parse_query('value:10..*')), 'Xapian::Query(VALUE_RANGE 14 000000000010 %012d)' % sys.maxint)
        self.assertEqual(str(self.backend.parse_query('popularity:25.5..100.0')), 'Xapian::Query(VALUE_RANGE 7 \xb2` \xba@)')


class LiveXapianMockSearchIndex(indexes.SearchIndex):
    text = indexes.CharField(document=True, use_template=True)
    name = indexes.CharField(model_attr='author')
    pub_date = indexes.DateField(model_attr='pub_date')
    created = indexes.DateField()
    title = indexes.CharField()

    def get_model(self):
        return MockModel


class LiveXapianSearchQueryTestCase(TestCase):
    """
    SearchQuery specific tests
    """
    fixtures = ['initial_data.json']

    def setUp(self):
        super(LiveXapianSearchQueryTestCase, self).setUp()

        self.old_ui = connections['default'].get_unified_index()
        ui = UnifiedIndex()
        index = LiveXapianMockSearchIndex()
        ui.build(indexes=[index])
        self.backend = connections['default'].get_backend()
        connections['default']._index = ui
        self.backend.update(index, MockModel.objects.all())

        self.sq = connections['default'].get_query()

    def tearDown(self):
        self.backend.clear()
        connections['default']._index = self.old_ui
        super(LiveXapianSearchQueryTestCase, self).tearDown()

    def test_get_spelling(self):
        self.sq.add_filter(SQ(content='indxd'))
        self.assertEqual(self.sq.get_spelling_suggestion(), u'indexed')
        self.assertEqual(self.sq.get_spelling_suggestion('indxd'), u'indexed')

    def test_startswith(self):
        self.sq.add_filter(SQ(name__startswith='da'))
        self.assertEqual([result.pk for result in self.sq.get_results()], [1, 2, 3])

    def test_build_query_gt(self):
        self.sq.add_filter(SQ(name__gt='m'))
        self.assertEqual(str(self.sq.build_query()), u'Xapian::Query((<alldocuments> AND_NOT VALUE_RANGE 2 a m))')

    def test_build_query_gte(self):
        self.sq.add_filter(SQ(name__gte='m'))
        self.assertEqual(str(self.sq.build_query()), u'Xapian::Query(VALUE_RANGE 2 m zzzzzzzzzzzzzzzzzzzzzzzzzzzz'
                                                     u'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'
                                                     u'zzzzzzzzzzzzzz)')

    def test_build_query_lt(self):
        self.sq.add_filter(SQ(name__lt='m'))
        self.assertEqual(str(self.sq.build_query()), u'Xapian::Query((<alldocuments> AND_NOT VALUE_RANGE 2 m zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz))')

    def test_build_query_lte(self):
        self.sq.add_filter(SQ(name__lte='m'))
        self.assertEqual(str(self.sq.build_query()), u'Xapian::Query(VALUE_RANGE 2 a m)')

    def test_build_query_multiple_filter_types(self):
        self.sq.add_filter(SQ(content='why'))
        self.sq.add_filter(SQ(pub_date__lte=datetime.datetime(2009, 2, 10, 1, 59, 0)))
        self.sq.add_filter(SQ(name__gt='david'))
        self.sq.add_filter(SQ(created__lt=datetime.datetime(2009, 2, 12, 12, 13, 0)))
        self.sq.add_filter(SQ(title__gte='B'))
        self.sq.add_filter(SQ(id__in=[1, 2, 3]))
        self.assertEqual(str(self.sq.build_query()), u'Xapian::Query(((Zwhi OR why) AND VALUE_RANGE 3 00010101000000 20090210015900 AND (<alldocuments> AND_NOT VALUE_RANGE 2 a david) AND (<alldocuments> AND_NOT VALUE_RANGE 1 20090212121300 99990101000000) AND VALUE_RANGE 5 b zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz AND (Q1 OR Q2 OR Q3)))')

    def test_log_query(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)

        # Stow.
        old_debug = settings.DEBUG
        settings.DEBUG = False

        len(self.sq.get_results())
        self.assertEqual(len(connections['default'].queries), 0)

        settings.DEBUG = True
        # Redefine it to clear out the cached results.
        self.sq = connections['default'].get_query()
        self.sq.add_filter(SQ(name='bar'))
        len(self.sq.get_results())
        self.assertEqual(len(connections['default'].queries), 1)
        self.assertEqual(str(connections['default'].queries[0]['query_string']), u'Xapian::Query((ZXNAMEbar OR XNAMEbar))')

        # And again, for good measure.
        self.sq = connections['default'].get_query()
        self.sq.add_filter(SQ(name='bar'))
        self.sq.add_filter(SQ(text='moof'))
        len(self.sq.get_results())
        self.assertEqual(len(connections['default'].queries), 2)
        self.assertEqual(str(connections['default'].queries[0]['query_string']), u'Xapian::Query((ZXNAMEbar OR XNAMEbar))')
        self.assertEqual(str(connections['default'].queries[1]['query_string']), u'Xapian::Query(((ZXNAMEbar OR XNAMEbar) AND (ZXTEXTmoof OR XTEXTmoof)))')

        # Restore.
        settings.DEBUG = old_debug


class LiveXapianSearchQuerySetTestCase(TestCase):
    """
    SearchQuerySet specific tests
    """
    fixtures = ['initial_data.json']

    def setUp(self):
        super(LiveXapianSearchQuerySetTestCase, self).setUp()

        self.old_ui = connections['default'].get_unified_index()
        self.ui = UnifiedIndex()
        self.index = LiveXapianMockSearchIndex()
        self.ui.build(indexes=[self.index])
        self.backend = connections['default'].get_backend()
        connections['default']._index = self.ui
        self.backend.update(self.index, MockModel.objects.all())

        self.sq = connections['default'].get_query()
        self.sqs = SearchQuerySet()

    def tearDown(self):
        self.backend.clear()
        connections['default']._index = self.old_ui
        super(LiveXapianSearchQuerySetTestCase, self).tearDown()

    def test_result_class(self):
        # Assert that we're defaulting to ``SearchResult``.
        sqs = self.sqs.all()
        self.assertTrue(isinstance(sqs[0], SearchResult))

        # Custom class.
        sqs = self.sqs.result_class(MockSearchResult).all()
        self.assertTrue(isinstance(sqs[0], MockSearchResult))

        # Reset to default.
        sqs = self.sqs.result_class(None).all()
        self.assertTrue(isinstance(sqs[0], SearchResult))


class XapianBoostBackendTestCase(TestCase):
    def setUp(self):
        super(XapianBoostBackendTestCase, self).setUp()

        self.old_ui = connections['default'].get_unified_index()
        self.ui = UnifiedIndex()
        self.index = XapianBoostMockSearchIndex()
        self.ui.build(indexes=[self.index])
        self.backend = connections['default'].get_backend()
        connections['default']._index = self.ui

        self.sample_objs = []

        for i in xrange(1, 5):
            mock = AFourthMockModel()
            mock.id = i
            if i % 2:
                mock.author = 'daniel'
                mock.editor = 'david'
            else:
                mock.author = 'david'
                mock.editor = 'daniel'
            mock.pub_date = datetime.date(2009, 2, 25) - datetime.timedelta(days=i)
            self.sample_objs.append(mock)

        self.backend.update(self.index, self.sample_objs)

    def tearDown(self):
        self.backend.clear()
        connections['default']._index = self.old_ui
        super(XapianBoostBackendTestCase, self).tearDown()

    def test_boost(self):
        sqs = SearchQuerySet()

        self.assertEqual(len(sqs.all()), 4)

        results = sqs.filter(SQ(author='daniel') | SQ(editor='daniel'))

        self.assertEqual([result.id for result in results], [
            'core.afourthmockmodel.1',
            'core.afourthmockmodel.3',
            'core.afourthmockmodel.2',
            'core.afourthmockmodel.4'
        ])
