import sys
import os
import numpy as np

# Add backend to path so we can import app modules if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sentence_transformers import SentenceTransformer

def cosine_similarity(v1, v2):
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

def evaluate_model(model_name):
    print(f"\n{'='*50}\nEvaluating model: {model_name}\n{'='*50}")
    model = SentenceTransformer(model_name)
    
    test_cases = {
        "Negation": [
            ("Implement rate limiting", "Remove rate limiting"),
            ("Turn on caching", "Disable caching"),
            ("Allow user access", "Deny user access")
        ],
        "Binding/Role Reversal": [
            ("Admin deletes user posts", "User deletes admin posts"),
            ("Client sends message to server", "Server sends message to client"),
            ("Manager assigns task to employee", "Employee assigns task to manager")
        ],
        "Scope/Context": [
            ("Add auth to public API", "Remove auth from internal API"),
            ("Optimize frontend render loop", "Optimize backend database query"),
            ("Fix bug in the billing service", "Fix bug in the logging service")
        ],
        "Semantic Similarity (Expected High)": [
            ("Increase performance of the app", "Make the application faster"),
            ("Delete the database table", "Drop the sql table"),
        ]
    }
    
    results = {}
    for category, pairs in test_cases.items():
        print(f"\n--- {category} ---")
        cat_results = []
        for text1, text2 in pairs:
            v1 = model.encode(text1, normalize_embeddings=True)
            v2 = model.encode(text2, normalize_embeddings=True)
            sim = cosine_similarity(v1, v2)
            cat_results.append(sim)
            print(f"[{sim:.4f}] '{text1}'  vs  '{text2}'")
        
        results[category] = np.mean(cat_results)
        print(f"Average for {category}: {results[category]:.4f}")
        
    return results

if __name__ == "__main__":
    current_model = "all-MiniLM-L6-v2"
    proposed_model = "BAAI/bge-small-en-v1.5"
    
    model1_res = evaluate_model(current_model)
    model2_res = evaluate_model(proposed_model)

