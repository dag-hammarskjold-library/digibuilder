from flask import Flask, render_template, request, abort, Response, flash, redirect, jsonify
from pymarc import record, MARCReader, marcxml
from pymarc.record import Field
#import requests
from lxml import etree
from lxml.etree import tostring
from lxml.html.soupparser import fromstring
from io import StringIO, BytesIO
import ssl
from urllib import request as req
import urllib
import boto3, botocore
import re, os
import csv
#from .symbol import make_symbol, resolve
from werkzeug.utils import secure_filename
import requests

app = Flask(__name__)
app.secret_key = os.urandom(24)
session = boto3.Session(profile_name='dhldigitization')

# these two classes are taken from url_resolver
class PageNotFoundException(Exception):
    pass

class MARCXmlParse:
    '''
        given a url, e.g.
            https://digitallibrary.un.org/record/696939/export/xm
        parse the xml via pymarc.parse_xml_to_array
        use pymarc to pull out fields:
            035
    '''
    def __init__(self, url):
        resp = req.urlopen(url, context=ssl._create_unverified_context())
        if resp.status != 200:
            raise PageNotFoundException("Could not get data from {}".format(url))
        self.xml_doc = BytesIO(resp.read())
        #print(self.xml_doc)
        r = marcxml.parse_xml_to_array(self.xml_doc, False, 'NFC')
        #print(r)
        if len(r) > 0:
            self.record = r[0]
        else:
            self.record = None

    def field_035(self):
        return self.record.get_fields('035')

    def field_191(self):
        return self.record.get_fields('191')

@app.route('/', methods=['GET','POST'])
def index():
    if request.method == 'POST':
        this_path = request.form.get('filePath',None)
        this_191b = request.form.get('191b',None)
        this_191c = request.form.get('191c',None)
        # take in a CSV in the format symbol,filename
        # also take in a global set of fields for the batch: path, 191__b, and 191__c
        if 'file' not in request.files:
            print('No file part')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            print('No selected file')
            return redirect(request.url)
        file_contents = file.stream.read().decode('utf-8',errors='replace')
        #print(file_contents)
        this_csv = csv.DictReader(file_contents.splitlines())
        return_data = []
        for row in this_csv:
            #print(row)
            record_defaults = {'245__a': '', '269__a': '', '029__b': '', '300__a': '', '999__a': '', '931__a': 'NY - Digitization', '980__a': 'BIB', '989__a': 'Documents and Publications', '990__a': 'New'}
            this_row = {}
            for tag in ['029$b Job number','1.191$a Doc symbol','1.191$b main body','1.191$c Session','245$a Title','260$c Date published','300$a Pages','1.999$a Creator/Date','file name']:
                #print(tag)
                #tag = tag.replace(u'\U0000FEFF','')
                #print(tag)
                if tag == 'file name':
                    #print(tag)
                    this_row['FFT__n'] = row[tag]
                elif '.' in tag:
                    # we can try to figure out if there are multiple symbols
                    # they would be entered, e.g., as 191__a-1 and 191__a-2 in the output sheet.
                    tag_seq = tag.split('.')[0]
                    tag_txt = tag.split('.')[1].split(' ')[0].replace('$','__')
                    output_header = tag_txt + '-' + tag_seq
                    #print(row[tag])
                    this_row[output_header] = row[tag]
                else:
                    this_t = tag.split(' ')[0].replace('$','__')
                    this_row[this_t] = row[tag]
            #print(this_row)
        # this_new.append({**{'191__a':symbol, 'FFT__a': '', 'FFT__n': '', 'this_path': file_path, 'this_entry': entry }, **record_defaults})
            return_data.append({**record_defaults, **this_row})
        #for row in this_csv:
        #    return_data.append({**{'191__a':row[1], '191__b': this_191b, '191__c': this_191c, 'FFT__n': row[0]}, **record_defaults})
        print(return_data)
        return render_template('review.html', context = {'new': return_data, 'file_path': this_path.split(':\\')[1].replace('\\','/').strip()})
    else:
        return render_template('index.html')

@app.route('/download')
def download():
    pass

@app.route('/s3')
def validate():
    s3 = session.resource('s3')
    #file_path = request.args.get('path')
    entry = request.args.get('entry')
    search_key = request.args.get('path') + entry
    # Validate the file name in S3
    this_object = s3.Object('digitization', search_key)
    print(this_object)
    this_url = None
    try:
        this_object.load()
    except botocore.exceptions.ClientError as e:
        #print("Not found")
        this_url = None
        #raise PageNotFoundException("Cannot find the requested object.")
        return jsonify('')
    else:
        this_url = 'https://s3.amazonaws.com/digitization/' + search_key.replace(' ','%20')
        #this_filename = None
    if this_url:
        #this_filename = this_url.split('/')[-1]
        print(this_url)
    return jsonify(this_url)

@app.route('/resolve')
def resolve():
    resolver_endpoint = 'https://9inpseo1ah.execute-api.us-east-1.amazonaws.com/prod/symbol/'
    symbol = request.args.get('symbol')
    # find out if the symbol exists
    # Sample: A/RES/45/110
    # Go to resolver_endpoint + symbol, 
    resolvable = requests.get(resolver_endpoint + symbol)
    if resolvable.status_code == 200:
        this_data = resolvable.text
        # try with lxmx, since ElementTree chokes on the generated HTML
        root = fromstring(this_data).find('.//a[@id="link-lang-select"]')
        #exists, then use xpath: //*[@id="link-lang-select"]
        undl_link = root.attrib['href']
        # Drop the query string (?ln=en) and add /export/xm
        undl_xml_url = undl_link.split('?')[0] + '/export/xm'
        parser = MARCXmlParse(undl_xml_url)
        #print(parser.field_035())
        for this_035 in parser.field_035():
            if '(DHL)' in this_035['a']:
                print(this_035['a'])
                return jsonify(this_035['a'])
    elif resolvable.status_code == 404:
        # as constructed, this symbol is definitely not found
        # but if we strip out punctuation, we can search against 191__q, e.g., A/72/150 becomes A72150
        # Search against the UNDL endpoint here: https://digitallibrary.un.org/search?ln=en&p=191__q%3A%22e2953%22&f=&action_search=Search&rm=&ln=en&fti=0&sf=&so=d&rg=10&c=United+Nations+Digital+Library+System&of=xm&ot=035,191
        # if it doesn't exist in UNDL, we put it in the INSERT spreadsheet
        this_symbol = re.sub(r'[\W_]','',symbol)
        #print(this_symbol)
        undl_search_term = "https://digitallibrary.un.org/search?ln=en&p=191__q%3A%22" + this_symbol + "%22&f=&action_search=Search&rm=&ln=en&fti=0&sf=&so=d&rg=10&c=United+Nations+Digital+Library+System&of=xm&ot=035,191"
        #print(undl_search_term)
        #this_resolvable = requests.get(undl_search_term, verify=False)
        parser = MARCXmlParse(undl_search_term)
        #print(parser.record)
        if parser.record is not None:
            if parser.field_191() is not None:
                #we found a result with this symbol
                for this_035 in parser.field_035():
                    if '(DHL)' in this_035['a']:
                        print(this_035['a'])
                        return jsonify(this_035['a'])
            else:
                #we found nothing matching
                return jsonify('')
        else:
            # zero results for this search
            return jsonify('')
    else:
        # we can't really know, since the other error codes don't tell us that kind of info
        return jsonify('')