import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

# 1. Inicializamos la conexión con Firebase
# El archivo credenciales.json debe estar en la misma carpeta que este main.py
cred = credentials.Certificate("credenciales.json")
firebase_admin.initialize_app(cred)

# Creamos el cliente para interactuar con la base de datos
db = firestore.client()

app = FastAPI(title="Sistema de Pedidos - Cafetería")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción se pone el dominio real, acá permitimos todos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ItemPedido(BaseModel):
    producto: str
    cantidad: int
    precio_unitario: float

class Pedido(BaseModel):
    mesa: int
    items: List[ItemPedido]
    notas: str = ""
@app.get("/obtener_catalogo")
async def obtener_catalogo():
    # Leemos todos los documentos de la colección "productos"
    referencia = db.collection("productos").stream()
    
    lista_productos = []
    for doc in referencia:
        producto_db = doc.to_dict()
        lista_productos.append({
            "producto": producto_db["nombre"],
            "precio": producto_db["precio"]
        })
        
    return {"catalogo": lista_productos}
@app.post("/crear_pedido")
async def recibir_pedido(pedido: Pedido):
    total_calculado = sum(item.cantidad * item.precio_unitario for item in pedido.items)
    
    nuevo_pedido_db = {
        "mesa": pedido.mesa,
        "items": [item.model_dump() for item in pedido.items],
        "notas": pedido.notas,
        "total": total_calculado,
        "estado": "pendiente"
    }
    
    # 2. Guardamos el pedido en la colección 'pedidos' de Firestore
    # add() devuelve la fecha de creación y la referencia del documento creado
    hora_creacion, ref_documento = db.collection("pedidos").add(nuevo_pedido_db)
    
    print(f"Pedido guardado en Firestore con ID: {ref_documento.id}")
    
    return {
        "status": "éxito",
        "mensaje": "Pedido enviado a la cocina",
        "pedido_id": ref_documento.id
    }