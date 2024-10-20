import json
from pprint import pprint
import os
import time

from elasticsearch import Elasticsearch
from sentence_transformers import SentenceTransformer

from langchain_community.document_loaders import UnstructuredPDFLoader, OnlinePDFLoader, PyPDFLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import os


class Search:
    def __init__(self):
        load_dotenv()

        loader = UnstructuredPDFLoader("national_llm.pdf")
        data = loader.load()

        print (f'You have {len(data)} document(s) in your data')
        print (f'There are {len(data[0].page_content)} characters in your sample document')
        print (f'Here is a sample: {data[0].page_content[:200]}')

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        self.texts = text_splitter.split_documents(data)

        # Let's see how many small chunks we have
        print (f'Now you have {len(texts)} documents')

        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.es = Elasticsearch('http://localhost:9200')  # Connect to a Self-Hosted Elasticsearch Docker Container        
        client_info = self.es.info()
        print('Connected to Elasticsearch!')
        pprint(client_info.body)

    def create_index(self):
        self.es.indices.delete(index='my_documents', ignore_unavailable=True)
        self.es.indices.create(
            index='my_documents',
            mappings={
                'properties': {
                    'embedding': {
                        'type': 'dense_vector',
                    },
                    'elser_embedding': {
                        'type': 'sparse_vector',
                    },
                }
            },
            settings={
                'index': {
                    'default_pipeline': 'elser-ingest-pipeline'
                }
            }
        )

    def get_embedding(self, text):
        return self.model.encode(text)

    def insert_document(self, document):
        return self.es.index(index='my_documents', document={
            **document,
            'embedding': self.get_embedding(document['summary']),
        })

    def insert_documents(self, documents):
        operations = []
        for document in documents:
            operations.append({'index': {'_index': 'my_documents'}})
            operations.append({
                **document,
                'embedding': self.get_embedding(document['summary']),
            })
        return self.es.bulk(operations=operations)

    # def reindex(self):
    #     self.create_index()
    #     with open('data.json', 'rt') as f:
    #         documents = json.loads(f.read())
    #     return self.insert_documents(documents)

    def reindex(self):
        self.create_index()
        return self.insert_documents(self.texts)

    def retrieve_document(self, id):
        return self.es.get(index='my_documents', id=id)
    
    def deploy_elser(self):
        # download ELSER v2
        self.es.ml.put_trained_model(model_id='.elser_model_2',
                                     input={'field_names': ['text_field']})
        
        # wait until ready
        while True:
            status = self.es.ml.get_trained_models(model_id='.elser_model_2',
                                                   include='definition_status')
            if status['trained_model_configs'][0]['fully_defined']:
                # model is ready
                break
            time.sleep(1)

        # deploy the model
        self.es.ml.start_trained_model_deployment(model_id='.elser_model_2')

        # define a pipeline
        self.es.ingest.put_pipeline(
            id='elser-ingest-pipeline',
            processors=[
                {
                    'inference': {
                        'model_id': '.elser_model_2',
                        'input_output': [
                            {
                                'input_field': 'summary',
                                'output_field': 'elser_embedding',
                            }
                        ]
                    }
                }
            ]
        )

    def search(self, **query_args):
        # sub_searches is not currently supported in the client, so we send
        # search requests as raw requests
        if 'from_' in query_args:
            query_args['from'] = query_args['from_']
            del query_args['from_']
        return self.es.perform_request(
            'GET',
            f'/my_documents/_search',
            body=json.dumps(query_args),
            headers={'Content-Type': 'application/json',
                     'Accept': 'application/json'},
        )
    