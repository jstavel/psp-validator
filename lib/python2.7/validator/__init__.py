# -*- coding: utf-8 -*-
from settings import logger, schema_catalog, workdir
import re, os.path
from .catalog import Catalog
from lxml import etree
from functools import partial
import hashlib

def get_short_description(doc):
    result = re.search("^[\ \t]*([^\n]+)", doc, re.MULTILINE)
    return result.group(1)

 
class Validator(object):
    def __init__(self, psp, **kwargs):
        self.psp = psp
        self.kwargs = kwargs
        self.catalog = Catalog()
        self.results = []

    @classmethod
    def short_desc_of_validator(self,name):
        methods = [ 'validate_' + v for v in self.validators() if v == name ]
        if methods:
            m = getattr(self,methods[0])
            return get_short_description(m.__doc__)
        return None

    @classmethod
    def desc_of_validator(self,name):
        methods = [ 'validate_' + v for v in self.validators() if v == name ]
        if methods:
            m = getattr(self,methods[0])
            return m.__doc__
        return None

    @classmethod
    def validators(self):
        method_names = [ m for m in dir(self) if 'validate_' in m ]
        method_names.sort()
        return [ m.split("validate_")[1] for m in method_names ]

    def run_validator(self, name):
        methods = [ 'validate_' . v for v in self.validators() if v == name ]
        if methods:
            m = methods[0]
            logger.info("validator: %s" % (get_short_description(m.__doc__)))
            m()
        pass

    def for_each(self, items, validator):
        if len(items) == 0:
            logger.error("neni zadny element")
            return False
        
        all_valid = True
        for item in items:
            logger.debug('%s: %s' % (validator.__doc__,str(item),))
            error = validator(self,item)
            if error:
                logger.error("chyba validace: %s" % (error,))
                all_valid = False
        return all_valid
        
    @property
    def summary(self):
        return self.results

    def validate(self,method=None):
        """ zavolá všechny validace, co odpovídají argumentům programu.
        Pokud je zadana metoda. Zavola jen jedno dotycnou validaci.
        napr: 01_mets
        """
        if method:
            logger.debug('vybrana metoda validace: ' + method)
        method_names = [ m for m in dir(self) if 'validate_' in m ]
        method_names.sort()
        methods = [ (getattr(self,m),m) for m in method_names if (method and m == ('validate_' + method)) or (not method) ]
        max_width = max( [len(get_short_description(m[0].__doc__)) for m in methods ] )
        out_format = "validator: {:<" + str(max_width) + '} *{:s}*'
        for (m,name) in methods:
            logger.info(out_format.format(get_short_description(m.__doc__), name.split('validate_')[1]))
            result = m()
            self.results.append({'validator': name.split('validate_')[1], 'result': result})
        pass

    def validate_01_mets(self):
        """ validace METS souboru
        validuje METS soubor podle specifikace METS"""
        mets = self.psp.mets
        schema = self.catalog.mets
        logger.debug("volam schema.validate")
        result = schema.validate(mets.etree)
        logger.debug("vysledek schema.validate:" + str(result))
        if not result:
            logger.error("validace souboru %s skoncila s chybou: %s" % (str(mets), schema.error_log))
        return result

    def validate_01_mets_mods(self):
        """ validace vnitřku METS souboru
        validuje vnitřní položky MODS v METS soubor podle specifikace MODS"""
        mets = self.psp.mets
        elems = mets.etree.xpath("//mods:mods",  namespaces = self.catalog.namespaces)
        if len(elems) == 0:
            logger.error("nenasel jsem zadnou mods polozku")
            return False
        
        logger.debug('nalezene mods elementy: ' + str(elems))
        
        schema = self.catalog.mods
        results = [ schema.validate(m) and {'success':True, 'msg': ""} or {'success': False, 'msg': schema.error_log } for m in elems ]
        logger.debug("vysledky schema.validate:" + str(results))
        some_error = False in [ r['success']  for r in results ]
        if some_error:
            logger.error('chyby validace: ' + str([r['msg'] for r in results if r]))
            return False
        return True

    def validate_01_mets_dc(self):
        """ validace vnitřku METS souboru, specifikace DC
        validuje vnitřní položky DC v METS soubor podle specifikace Dublin Core"""
        mets = self.psp.mets
        elems = mets.etree.xpath("//dc:dc", namespaces = self.catalog.namespaces)
        if len(elems) == 0:
            logger.error("nenasel jsem zadnou Dublin Core polozku")
            return False

        logger.debug('nalezene DC elementy: ' + str(elems))
        
        schema = self.catalog.dc
        results = [ schema.validate(m) and {'success':True, 'msg': ""} or {'success': False, 'msg': schema.error_log } for m in elems ]
        logger.debug("vysledky schema.validate:" + str(results))
        some_error = False in [ r['success']  for r in results ]
        if some_error:
            logger.error('chyby validace: ' + str([r['msg'] for r in results if r]))
            return False
        return True

    def validate_02_links_exist(self):
        """ validace linek v souboru mets
        zkontroluje, zda existují všechny soubory na které se odkazují linky v souboru METS."""
        
        hrefs = self.psp.mets.xpath("//mets:file/mets:FLocat/@xlink:href", namespaces=self.catalog.namespaces)

        def validator(self,href):
            """test, jestli soubor existuje"""
            error_msg = None
            if not self.psp.exists(href):
                error_msg = "tento soubor neexistuje: " + str(href)
                return error_msg
        return self.for_each(hrefs, validator)

    def validate_02_links_checksums(self):
        """ kontrola CHECKSUM všech souborů na které se v METS souboru odkazuje
        zkontroluje, zda mají soubory, na které linky odkazují, správnou CHECKSUM."""

        mets_files = self.psp.mets.xpath("//mets:file", namespaces=self.catalog.namespaces)

        def validator(self,mets_file):
            declared_checksum = mets_file.xpath("./@CHECKSUM")[0]
            logger.debug('uvedene CHECKSUM je: %s' % (declared_checksum,))
            fpath = mets_file.xpath("./mets:FLocat/@xlink:href", namespaces = self.catalog.namespaces)[0]
            m = hashlib.md5()
            logger.debug('fpath je: %s' % (fpath,))
            fh = open(self.psp.join(fpath), 'rb')
            while True:
                data = fh.read(8192)
                if not data:
                    break
                m.update(data)
            real_checksum = m.hexdigest()
            if declared_checksum != real_checksum:
                return "soubor %s ma jiny CHECKSUM, nez je napsano" % (fpath,)
            return None
        return self.for_each(mets_files, validator)
   
    def validate_03_techspecs(self):
        """ validace souboru amdSec s technickými metadaty
        zkontroluje předběžně jednotlivé soubory ve složce amdSec"""

        paths = self.psp.mets.xpath("//mets:fileGrp[@ID='TECHMDGRP']/mets:file/mets:FLocat/@xlink:href",
                                    namespaces = self.catalog.namespaces
                                    )
        fpaths = [ self.psp.join(fp) for fp in paths ]
                      
        def validator(self,fpath):
            """validace souboru podle schematu METS. Elementy PREMIS a MIX se validuji samostatne v dalsim validatoru."""
            logger.debug("validuji: " + fpath)
            xml_file = etree.parse(fpath)
            
            """ prehodime xsi:type na type. Pak projde validace pres METS. premis:object zvalidujeme v puvodni verzi v dalsim validatoru. """
            for elem in xml_file.xpath("//premis:object", namespaces = self.catalog.namespaces):
                uri = self.catalog.namespaces['xsi']
                name = "{%s}type" % (uri,)
                if name in elem.attrib.keys():
                    xsi_type = elem.attrib[name]
                    del elem.attrib[name]
                    elem.attrib['type'] = xsi_type
                    
            result = self.catalog.mets.validate(xml_file)
            if not result:
                return self.catalog.mets.error_log
            return None
                
        #return self.for_each([fpaths[0]], validator)
        return self.for_each(fpaths, validator)

    def validate_03_techspecs_premis_mix(self):
        """ validace souboru amdSec na technická metadata
        zkontroluje, zda jednotlivé soubory odpovídají použitým schematům.
        To je formát METS a vevnitřs jsou polozky AMD - podle schematu PREMIS v2.1. a položky ve formatu MIX"""

        paths = self.psp.mets.xpath("//mets:fileGrp[@ID='TECHMDGRP']/mets:file/mets:FLocat/@xlink:href", 
                                    namespaces = self.catalog.namespaces)
        fpaths = [ self.psp.join(fp) for fp in paths ]

        def validator(self,fpath):
            """validace jednotlivých PREMIS a MIX částí"""
            logger.debug("validuji: " + fpath)
            premis = self.catalog.premis
            mix = self.catalog.mix
            xml_file = etree.parse(fpath)
            error = None

            # premis elementy
            elems = xml_file.xpath("/*/*/*/mets:mdWrap[@MDTYPE='PREMIS']/mets:xmlData/premis:object", namespaces = self.catalog.namespaces)
            results = [ premis.validate(elem) and {'success':True, 'msg': ""} or {'success': False, 'msg': premis.error_log } for elem in elems ]
            logger.debug("vysledky schema.validate:" + str(results))
            some_error = False in [ r['success']  for r in results ]
            if some_error:
                error = 'chyby validace souboru %s: %s' % (fpath,"|".join([str(r['msg']) for r in results if r]))
                
            # mix elementy
            elems = xml_file.xpath("/*/*/*/mets:mdWrap/mets:xmlData/mix:mix", namespaces = self.catalog.namespaces)
            results = [ mix.validate(elem) and {'success':True, 'msg': ""} or {'success': False, 'msg': mix.error_log } for elem in elems ]
            logger.debug("vysledky schema.validate:" + str(results))
            some_error = False in [ r['success']  for r in results ]
            if some_error:
                error = (error or "") + 'chyby validace souboru %s: %s' % (fpath,"|".join([str(r['msg']) for r in results if r]))

            return error
        
        return self.for_each(fpaths, validator)
        #return self.for_each([fpaths[0]], validator)

    def validate_04_altos(self):
        """ validace souboru ALTO_ se skenem
        zkontroluje predbezne cely soubor ve slozce ALTO podle schematu ALTO"""

        paths = self.psp.mets.xpath("//mets:fileGrp[@ID='ALTOGRP']/mets:file/mets:FLocat/@xlink:href",
                                    namespaces = self.catalog.namespaces)
        fpaths = [ self.psp.join(fp) for fp in paths ]
                      
        def validator(self,fpath):
            """validace souboru"""
            xml_file = etree.parse(fpath)
            result = self.catalog.alto.validate(xml_file)
            if not result:
                return self.catalog.alto.error_log
            return None

        return self.for_each(fpaths, validator)

    
