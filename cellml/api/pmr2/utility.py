from os import listdir

from os.path import join
from os.path import dirname
from os.path import splitext

import urllib2
import urlparse

import zope.interface
from zope.schema.fieldproperty import FieldProperty

from cellml_api import CellML_APISPEC
from cellml_api import CeLEDSExporter

from cellml.api.pmr2.interfaces import ICellMLAPIUtility

_root = dirname(__file__)
resource_file = lambda *p: join(_root, 'resource', *p)


class CellMLAPIUtility(object):
    """\
    A more pythonic wrapper for the CellML API Python bindings.

    This class provides a few useful utilities that simplifies the 
    interaction with the CellML API from within Python, such as a better
    model loader, providing a wrapper for getting a generator to the
    lists, and in this case provdes some integration with PMR2.

    If this class is registered within ZCA, the CellML API can be
    acquired with::
    
        from cellml.api.pmr2.interfaces import ICellMLAPIUtility
        import zope.component
        api_util = zope.component.getUtility(ICellMLAPIUtility)
    """

    zope.interface.implements(ICellMLAPIUtility)

    approved_protocol = FieldProperty(ICellMLAPIUtility['approved_protocol'])
    celeds_exporter = FieldProperty(ICellMLAPIUtility['celeds_exporter'])

    def __init__(self):
        # set non-primative defaults
        self.approved_protocol = ['http', 'https',]
        self.celeds_exporter = {}

        # maybe we could instantiate some of these on demand.
        self.cellml_bootstrap = CellML_APISPEC.CellMLBootstrap()
        self.model_loader = self.cellml_bootstrap.getmodelLoader()

        self.celeds_bootstrap = CeLEDSExporter.CeLEDSExporterBootstrap()
        # XXX workaround the bug in the API where the constructor for
        # this is broken.
        self.celeds_bootstrap = CeLEDSExporter.CeLEDSExporterBootstrap()

        # other initializations
        self._initiateCeLEDS()

    def _validateProtocol(self, location):
        return urlparse(location).scheme in self.approved_protocol

    def _initiateCeLEDS(self):
        """\
        Instantiate all CeLEDS definition files within the resource
        directory as exporters.
        """

        for filename in listdir(resource_file('celeds')):
            fd = open(resource_file('celeds', filename))
            raw = fd.read()
            fd.close()

            key, ext = splitext(filename)
            exporter = self.celeds_bootstrap.createExporterFromText(raw)

            self.celeds_exporter[key] = exporter

    def availableCeledsExporter(self):
        """
        """

        return self.celeds_exporter.key()

    def loadURL(self, location):
        if not self._validateProtocol:
            # XXX subclass this error may be better
            raise ValueError('protocol for the location is not approved')
        fd = urllib2.urlopen(location)
        result = fd.read()
        fd.close()
        return result

    def loadModel(self, model_url):
        """\
        Loads the CellML Model at the specified URL.

        This implementation does not use the built-in load model method
        in the model loader to avoid getting into non-reentrant issues
        that plagues that method.
        """

        def getImportGenerator(model):
            imports = model.getimports()
            importgen = makeGenerator(imports, 'Import')
            return importgen

        def appendQueue(base, model):
            # need to remember the source that this import was derived 
            # from, and use the xml:base of it if set.
            base_url = model.getxmlBase().getasText() or base
            subimport = list(getImportGenerator(model))
            if subimport:
                importq.append((base_url, subimport,))

        importq = []

        base = self.loadURL(model_url)
        model = self.model_loader.createFromText(base)
        appendQueue(model_url, model)

        while len(importq):
            # XXX base may be incorrect - check xml:base
            base, imports = importq.pop(0)
            for i in imports:
                relurl = i.getxlinkHref().getasText()
                nexturl = urlparse.urljoin(base, relurl)
                try:
                    base = self.loadURL(nexturl)
                except urllib2.URLError:
                    # XXX silently failing, should log somewhere
                    continue
                except ValueError:
                    # XXX subclass to better differentiate between this 
                    # and other ValueError
                    continue
                i.instantiateFromText(base)
                appendQueue(nexturl, i.getimportedModel())

        return model

    def serialiseNode(self, node):
        """\
        see Interface.
        """

        return self.cellml_bootstrap.serialiseNode(node)

    def extractMaths(self, model):
        """\
        see Interface.
        """

        results = []
        for component in makeGenerator(model.getallComponents(), 'Component'):
            results.append((
                component.getcmetaId() or component.getname(),
                [self.serialiseNode(i) 
                    for i in makeGenerator(component.getmath())],
            ))
        return results

    def exportCeleds(self, model, language=None):
        """\
        Export model to the target language(s) through CeLEDS.

        This uses the CellML Code Generation Service through the 
        CellML Language Export Definition Service exporter.

        model - the model object.
        language - a list of languages to generate output for.
                   if language is not available it will not be
                   used.
        """

        result = {}

        for key, exporter in self.celeds_exporter.iteritems():
            if language and k not in language:
                continue
            code = exporter.generateCode(model)
            result[key] = code

        return result


def makeGenerator(obj, key=None, iterator='iterate', next='next'):
    """\
    Takes the object and make a generator from a CellML API object.

    This is needed because the CellML API does not support standard
    iterators, and having them makes the API twice as easy to use.

    obj - object to attempt to get iterator from
    iterator - the iterator method
    next - the next method for the iterator
    key - short cut parameters; if set, this sets the iterator to be
          iterator${key}s and next to be next${key}
    """

    def _generator(func):
        while 1:
            next = func()
            if next is None:
                raise StopIteration
            yield next

    def getcallable(obj, name):
        if not hasattr(obj, name):
            raise TypeError("'%s' object is not iterable with %s" % 
                            (obj.__class__.__name__, name))
        result = getattr(obj, name)
        if not callable(result):
            raise TypeError("'%s.%s' is not callable" % 
                            (obj.__class__.__name__, name))
        return result

    if key is not None:
        iterator = 'iterate%ss' % key
        next = 'next%s' % key

    iobj = getcallable(obj, iterator)()
    nfunc = getcallable(iobj, next)
    return _generator(nfunc)
