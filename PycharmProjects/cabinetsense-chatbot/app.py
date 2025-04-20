import os, json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

from langchain.document_loaders import UnstructuredFileLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.chat_models import ChatOpenAI
from langchain.chains import RetrievalQA

# Load config
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME     = os.getenv("MODEL_NAME", "gpt-3.5-turbo")
DB_DIR         = os.getenv("CHROMA_DB_DIR", "./chroma_db")
NOTES_DIR      = os.getenv("KNOWLEDGE_DIR", "./cabinetsense-knowledgebase")
PORT           = int(os.getenv("PORT", 8000))

if not OPENAI_API_KEY:
    raise ValueError("Set OPENAI_API_KEY in your .env")

# 1. Load & split all docs
def load_and_split(directory):
    loaders = []
    for fn in os.listdir(directory):
        path = os.path.join(directory, fn)
        if fn.lower().endswith(".pdf"):
            loaders.append(UnstructuredFileLoader(path))
        elif fn.lower().endswith((".md", ".txt")):
            loaders.append(TextLoader(path))
    docs = []
    for ld in loaders:
        docs.extend(ld.load())
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    return splitter.split_documents(docs)

# 2. Embed & index into ChromaDB
def init_vectorstore(docs):
    emb = OpenAIEmbeddings()
    db = Chroma.from_documents(
        documents=docs,
        embedding=emb,
        persist_directory=DB_DIR,
        collection_name="cabinetsense_knowledgebase"
    )
    db.persist()
    return db

# 3. Build RetrievalQA chain
def build_qa_chain(db):
    llm = ChatOpenAI(model_name=MODEL_NAME, temperature=0)
    return RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=db.as_retriever(search_kwargs={"k": 3})
    )

# Startup: index everything
print("⏳ Loading & indexing knowledge base…")
_docs     = load_and_split(NOTES_DIR)
_vectordb = init_vectorstore(_docs)
_qa_chain = build_qa_chain(_vectordb)
print("✅ cabinetsense-chatbot ready!")

# FastAPI endpoints
app = FastAPI()

class ChatQuery(BaseModel):
    query: str

class Feedback(BaseModel):
    query: str
    bot_answer: str
    context_snippets: list
    user_id: str
    correct_answer: str = None

@app.post("/chat")
async def chat(body: ChatQuery):
    res = _qa_chain({"query": body.query})
    return {
        "answer": res["result"],
        "source_docs": [d.page_content for d in res["source_documents"]]
    }

@app.post("/feedback")
async def feedback(fb: Feedback):
    entry = fb.dict()
    try:
        with open("feedback_log.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "logged"}

# To run locally:
# uvicorn app:app --host 0.0.0.0 --port 8000
