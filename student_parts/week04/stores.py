from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from student_parts.week04.conversation_rag_store import ConversationMessageRAGStore
from fixed.reference_store import PersonalReferenceStore


REFERENCE_STORE = PersonalReferenceStore(CONFIG.chroma_dir)
SQLITE_STORE = AppSQLiteStore(CONFIG.app_db_path)
CONVERSATION_RAG_STORE = ConversationMessageRAGStore(CONFIG.chroma_dir)