'''
  Copyright (c) 2021-2024. Massachusetts Institute of Technology
  Notwithstanding any copyright notice, U.S. Government rights in this work are
  defined by DFARS 252.227-7013 or DFARS 252.227-7014 as detailed below. Use of
  this work other than as specifically authorized by the U.S. Government may
  violate any copyrights that exist in this work.

  UNLIMITED RIGHTS DFARS Clause reference: 252.227-7013 (a)(16) and
  252.227-7014 (a)(16) Unlimited Rights. The Government has the right to use,
  modify, reproduce, perform, display, release or disclose this (technical data
  or computer software) in whole or in part, in any manner, and for any purpose
  whatsoever, and to have or authorize others to do so.

  THE SOFTWARE IS PROVIDED TO YOU ON AN "AS IS" BASIS.
'''

# Notes:
# This code implements both
#     - Serving TIFF tiles (imagery) based on the Precomputed format,
#       including the assumed '.tiff' file extension.
#     - Proprietary annotation files in JSON format (refer to top-level
#       README for details).
#
# - URLs beginning with '/' (ie, /foo/bar) are relative to the host:port
# - URLs ending with '/' (ie, foo/) are directories relative to the current
#   request path.
# - URLs not ending with '/' (ie, foo) are potentially files with an implied
#   extension.  So, foo/bar could be interpreted foo/bar.tiff
# - It is important for the server to accept URLs without '/' that end up
#   being directories, and provide the path _from_ the client's request path.

import csv
import json
import logging
import os
from typing import Optional, Union
from fastapi import FastAPI, APIRouter, Request, Cookie, Header, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import tifffile

import models

router = APIRouter(
   # prefix = "",
   # tags = [""]
)

# Fastapi dev notes:
#
# Optional query params:  http://127.0.0.1:8000/items/5?q=somequery
# @app.get("/items/{item_id}")
# def read_item(item_id: int, q: Optional[str] = None):
#
# Returning errors:
# @app.get("/items/{item_id}")
#    ...
#    raise HTTPException(status_code=404, detail="Item not found")


# Global configuration class:
#
class Config:
   # Path where datasets are expected.  This is the docker/singularity
   # mapped volume.
   root_path = os.getenv("PRECOMPUTED_PATH", "/data")
   logfile = os.getenv("PRECOMPUTED_LOGGING", None)
   # Option for CORS middleware:
   origins = [ "*" ]


# Initialize logging after Config.  Note that uvicorn logging is configured
# from the command-line using uvicorn_log.yaml .
#
if Config.logfile is None:
   # Log to stdout:
   logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s',
                       level=logging.INFO)
else:
   # Log to specified file:
   try:
      logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s',
                          level=logging.INFO,
                          filename=Config.logfile, filemode='w')
   except Exception as e:
      logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s',
                          level=logging.INFO)
      logging.warning("Logging to stdout due to: %s" % str(e))


class Tools:
   '''
   Tools used to satisfy our implementation of Precomputed Service requirements.
   '''

   # Global dictionary. Caches info file from dataset(s).
   block_dict = {}


   def html_dir_listing(client_url_path:str)->str:
      '''
      Get a directory listing in HTML format.  This is normally consumed by a
      web browser and presented to a user.
      Args:
         client_url_path (str): Path to obtain directory listing for.
      Returns:
         str: HTML-formatted directory listing.
      Raises:
         HTTPException: If the client_url_path is not valid.
      '''
      logging.debug("Tools: html_dir_listing(): client_url_path=%s" % client_url_path)
      # Work on the filesystem path:
      full_fs_path = Config.root_path + client_url_path
      while full_fs_path.endswith('/'):
         full_fs_path = full_fs_path[:-1]
      logging.debug("Tools: html_dir_listing(): full_fs_path=%s" % full_fs_path)
      # Now work on return path parent URL:
      relative_parent = ""
      if not client_url_path.endswith("/"):
         relative_parent = client_url_path
         pos = relative_parent.rfind("/")
         if pos != -1:
            relative_parent = relative_parent.split('/')[-1]
         relative_parent = relative_parent + "/"
      # Check filesystem path existence:
      if not os.path.exists(full_fs_path):
         raise HTTPException(status_code=404, detail="%s not found" % \
                             relative_parent)
      entries = os.listdir(full_fs_path)
      entries.sort()
      html_content = """
      <html>
          <head>
              <title>Directory Listing</title>
          </head>
          <body>
             <h1>Directory Listing for %s:</h1>
      \n""" % client_url_path
      for entry in entries:
         hentry = relative_parent + entry
         # Check to see if it's a file or a directory.  Directory entries
         # will end in '/':
         if os.path.isdir(full_fs_path+'/'+entry):
            hentry = hentry + '/'
         logging.debug("Tools: Relative URL: %s" % hentry)
         html_content += "               <a href=%s>%s</a><br>\n" % \
                         (hentry,hentry)
      html_content += """
          </body>
      </html>
      """
      return html_content


   def get_full_file_path(req_path:str)->FileResponse:
      '''
      Resolve the absolute path and, if the path is valid, return the
      appropriate REST response (FileResponse) that will be returned
      to the REST client.
      Args:
         req_path (str): Absolute path of the file requested.
      Returns:
         FileResponse: If the req_path refers to a file.
      Raises:
         HTTPException: If the req_path is not valid.
      '''
      full_path = Config.root_path + "/" + req_path
      logging.debug("Full req path: %s" % full_path)
      while full_path.endswith('/'):
         full_path = full_path[:-1]
      if not os.path.exists(full_path):
         raise HTTPException(status_code=404, detail="%s not found" % req_path)
      return FileResponse(path=full_path)


   def get_block_size(info_file:str)->(int,int,int):
      '''
      Looks for block information in the specified 'info_file' CSV file.
      Typically, this will be 'block_info.csv' in a dataset.
      To improve performance, we keep the block information for each
      dataset that we've looked for in a dictionary.  The key in the
      cache/dictionary is the 'info_file'.
      For small datasets, the block_info.csv may refer to the entire
      dataset, so only one block is present.  For a larger dataset, it
      may be subdivided into multiple blocks where the size of each block
      is in block_info.csv.
      If the file is not found, this method returns (None,None,None)
      TODO: Currently a simple file that contains text "xxxx,yyyy,zzzz".
            For example:  1024,1024,600
      Args:
         info_file (str): Path to the info file (block_info.csv) of the dataset.
      Returns:
         (int,int,int): The x,y,z dimensions of a block in the dataset.
      '''
      val = None
      logging.debug("Get Block Size: info_file=%s" % info_file)
      if not info_file in Tools.block_dict:
         # Try to load the info from the file into the cache:
         try:
            with open(info_file, 'r') as csvfile:
               csvreader = csv.reader(csvfile)
               row1 = next(csvreader)
               val = [int(row1[0]),int(row1[1]),int(row1[2])]
               Tools.block_dict[info_file] = val
         except Exception as e:
            logging.warning("Exception while getting block size: %s" % str(e))
            return None,None,None
      else:
         # Found info in cache.  Return it.
         val = Tools.block_dict.get(info_file)
      logging.debug("Block size val=%s" % str(val))
      return val[0],val[1],val[2]


   def get_point_from_path(point_dir:str)->(int,int,int):
      '''
      Given a string (directory name within a path), extract the coordinate
      information as x,y,z and return the triplet.  If the path does not
      have such info, then return the None,None,None triplet.
      Args:
         point_dir (str): Encoded string that contains x,y,z coordinates.
                          The format is xNNNyMMMzPPP where NNN,MMM,PPP are
                          integers.
      Returns:
         (int,int,int): The x,y,z coordinates extracted from the string.
                        If the string is malformed, return None,None,None.
      '''
      # Make sure point_dir has x...y...z...:
      if point_dir[0] != 'x':
         logging.error("Parameter error in get_point_from_path(): path not point: %s" % point_dir)
         return None,None,None
      ypos = point_dir.find('y')
      # Handle legacy path:
      if ypos == -1:
         logging.error("Parameter error in get_point_from_path(): no 'y' in path: %s" % point_dir)
         return None,None,None
      zpos = point_dir.find('z')
      if zpos == -1:
         logging.error("Parameter error in get_point_from_path(): no 'z' in path: %s" % point_dir)
         return None,None,None
      xpoint = None
      try:
         xpoint = int(point_dir[1:ypos])
      except Exception as e:
         logging.error("Parameter error in get_point_from_path(): Invalid 'x': %s" % point_dir[1:ypos])
         return None,None,None
      ypoint = None
      try:
         ypoint = int(point_dir[ypos+1:zpos])
      except Exception as e:
         logging.error("Parameter error in get_point_from_path(): Invalid 'y': %s" % point_dir[ypos+1:zpos])
         return None,None,None
      zpoint = None
      try:
         zpoint = int(point_dir[zpos+1:])
      except Exception as e:
         logging.error("Parameter error in get_point_from_path(): Invalid 'z': %s" % point_dir[zpos+1:])
         return None,None,None
      # Looks like a valid xyz point path:
      return xpoint,ypoint,zpoint


   def translate_block_path(block_info_file:str, block:str)->str:
      '''
      Given an absolute point within the dataset, obtain the block
      directory where the point resides.  This is useful when a large
      dataset has been subdivided into smaller sections (blocks), and
      we need to retrieve the block for a given point.  This allows us
      to map the x,y,z point to a block xNNNN_yNNNN and replace the
      point in the path with the corresponding block.
      Args:
         block_info_file (str): The path to the block_info.csv file
         block (str): The point of interest within the full dataset.
                      It is encoded as "xNNNyMMMzPPP", where NNN,MMM,
                      and PPP are integers.
      Returns:
         str: The block directory where the given point resides.
              The format is "xNNNN_yMMMM" where NNNN and MMMM are
              integers.
      '''
      full_block_info_file = Config.root_path + "/" + block_info_file
      sizex,sizey,sizez = Tools.get_block_size(full_block_info_file)
      logging.debug("Block size = %s,%s,%s" % (str(sizex),str(sizey),str(sizez)))
      if sizex is None or sizey is None or sizez is None:
         return block
      pointx,pointy,pointz = Tools.get_point_from_path(block)
      if pointx is None or pointy is None or pointz is None:
         return block
      blockx = int(pointx/sizex)+1
      blocky = int(pointy/sizey)+1
      # TODO: We currently don't use subvolumes in 'z', but trivial to add:
      # blockz = int(pointz/sizez)+1
      # block_dir = "x%04d_y%04d_z%04d" % (blockx,blocky,blockz)
      block_dir = "x%04d_y%04d" % (blockx,blocky)
      return block_dir



@router.get("/index.html", response_class=HTMLResponse)
async def read_index(request: Request)->HTMLResponse:
   '''
   Return the directory listing when index.html was requested.
   Args:
      request (Request): The REST client's request.
   Returns:
      HTMLResponse: The directory listing in HTML format.
   Raises:
      HTTPException: If the requested path was not found.
   '''
   if not os.path.exists(Config.root_path):
      raise HTTPException(status_code=404,
                          detail="%s not found"%Config.root_path)
   # Top-level, so "index.html" is removed:
   logging.debug("Incoming request.url = %s" % str(request.url))
   # Top-level directory listing:
   html_content = Tools.html_dir_listing(client_url_path="")
   return HTMLResponse(content=html_content, status_code=200)


def tiff_to_byte_stream(req_path:str)->Response:
   '''
   Helper method that returns a TIFF file as a numpy array that NeuroTrale
   can consume.
   Args:
      req_path (str): The path to a TIFF file.
   Returns:
      Response: The tiff file byte stream.
   Raises:
      HTTPException: If the TIFF file was not found.
   '''
   try:
      chunk = tifffile.imread(Config.root_path + "/" + req_path)
      data = chunk.tostring("C")
      response = Response(content=data)
      response.headers["Content-type"] = "application/octet-stream"
      return response
   except Exception as e:
      logging.error("File not found: %s/%s" % (Config.root_path,req_path))
      raise HTTPException(status_code=404, detail="%s not found" % req_path)


@router.get("/{dataset_path}/{x}_{y}_{z}/{leaf_path}.tiff",
            responses = {
               200: {
                  "description": "OK",
                  "content": {
                     "image/tiff": {
                        "schema": {
                           "type": "string",
                           "format": "binary"
                        },
                        "examples": {
                           "sampleImage": {
                              "summary": "A sample image",
                              "value": ""
                           }
                        }
                     }
                  }
               }
            },
            response_class=FileResponse
           )
async def read_tiff_file(request: Request, dataset_path: str,
                       x:str, y:str, z:str, leaf_path: str):
   '''
   This serves files with the EXPLICIT TIFF file extension.
   Args:
      request (Request): The client's REST request object.
      dataset_path (str): The top-level path the identifies a dataset.
      x (str): The block's x location.
      y (str): The block's y location.
      z (str): The block's z location.
      leaf_path (str): The (leaf) filename, eg 16.tiff.
   Returns:
      Response: The tiff file byte stream.
   Raises:
      HTTPException: If the TIFF file was not found.
   '''
   scale = x + "_" + y + "_" + z
   req_path = dataset_path + "/" + scale + "/" + leaf_path + ".tiff"
   return tiff_to_byte_stream(req_path)


@router.get("/{dataset_path}/{x}_{y}_{z}/{leaf_path}",
            responses = {
               200: {
                  "description": "OK",
                  "content": {
                     "image/tiff": {
                        "schema": {
                           "type": "string",
                           "format": "binary"
                        },
                        "examples": {
                           "sampleImage": {
                              "summary": "A sample image",
                              "value": ""
                           }
                        }
                     }
                  }
               }
            },
            response_class=FileResponse
           )
async def read_tiff_file(request: Request, dataset_path: str,
                       x:str, y:str, z:str, leaf_path: str):
   '''
   Retrieve a TIFF file using an IMPLIED file extension.  The use of
   the implied file extension is identified via the x_y_z intermediate
   path.
   Args:
      request (Request): The client's REST request object.
      dataset_path (str): The top-level path the identifies a dataset.
      x (str): The block's x location.
      y (str): The block's y location.
      z (str): The block's z location.
      leaf_path (str): The (leaf) filename WITHOUT file extension, eg 16.
   Returns:
      Response: The tiff file byte stream.
   Raises:
      HTTPException: If the TIFF file was not found.
   '''
   scale = x + "_" + y + "_" + z
   req_path = dataset_path + "/" + scale + "/" + leaf_path + ".tiff"
   return tiff_to_byte_stream(req_path)


@router.get("/{dataset_path}/annotations/{block}/{leaf_file}.json",
            description="Retrieve the contents for the given JSON annotation file.",
            response_description="JSON object containing the annotations.",
            response_model=list[models.CentroidAnnotation|models.CellAnnotation|models.FiberAnnotation]
           )
async def read_json_file(request: Request, dataset_path: str, block: str,
                       leaf_file: str)->Response:
   '''
   Retrieve a JSON annotation file using an EXPLICIT file extension.
   We identify an annotation file requested by the 'annotations'
   intermediate path.
   Args:
      request (Request): The client's REST request object.
      dataset_path (str): The top-level path the identifies a dataset.
      block (str): The encoded intermediate block path (xNNNyMMMzPPP).
      leaf_file (str): The json file name.
   Returns:
      Response: The json file.
   Raises:
      HTTPException: If the json file was not found.
   '''
   block_path = Tools.translate_block_path( \
                  "%s/annotations/block_info.csv" % dataset_path,
                  block)
   req_path = dataset_path + "/annotations/" + block_path + "/" + \
             leaf_file + ".json"
   response = Tools.get_full_file_path(req_path=req_path)
   return response


@router.put("/{dataset_path}/annotations/{block}/{leaf_file}.json",
            description="Upload JSON contents to a file, overwriting an existng one.",
            response_description="JSON object containing the written file path.",
            response_model=models.Path
           )
async def put_json_file(request: Request, dataset_path: str, block: str,
                      leaf_file: str)->models.Path:
   '''
   Upload a JSON annotation file using an EXPLICIT file extension.
   We identify an annotation file path by the 'annotations' intermediate
   path.
   Args:
      request (Request): The client's REST request object.
      dataset_path (str): The top-level path the identifies a dataset.
      block (str): The encoded intermediate block path (xNNNyMMMzPPP).
      leaf_file (str): The json file name.
   Returns:
      str: json representing the filename that was written.
   '''
   block_path = Tools.translate_block_path( \
                  "%s/annotations/block_info.csv" % dataset_path,
                  block)
   req_path = dataset_path + "/annotations/" + block_path + "/" + \
             leaf_file + ".json"
   full_path = Config.root_path + "/" + req_path
   logging.info("Upload file: %s maps to %s" % (req_path,full_path))
   # NG is uploading the file in the body.  The fastapi.Body class doesn't
   # seem to be compatible with NG, so we'll read the content directly from the
   # Request:
   json_payload = b''
   async for chunk in request.stream():
      json_payload += chunk
   with open(full_path, 'wb') as f:
      f.write(json_payload)
   return model.Path(filename=req_path)


@router.get("/{some_path:path}",
            description="Retrieve a file. Note: .json and .tiff handled by a different router.",
            response_description="Contents of the requested file.",
            # response_class=FileResponse|HTMLResponse
            response_class=FileResponse
           )
async def read_some_path(request: Request, some_path:str)->Response:
   '''
   Catch-all for retrieving a file.  This is used when retrieving files
   that are not in a dataset or have other file extensions.  No path
   translation is performed here.
   Args:
      request (Request): The client's REST request object.
      some_path (str): The requested file/directory path.
   Returns:
      Response: The file or directory dontent.
   Raises:
      HTTPException: If the requested file was not found.
   '''
   url_path = "/" + some_path  # NOTE: Above, the app.get("/....") strips the leading '/'
   logging.debug("Default handler.  Client request path: %s" % url_path)
   if '..' in url_path:
      # Directory traversal attack
      logging.warning("Default Handler:  Directory traversal attack detected.  Raising 404.")
      raise HTTPException(status_code=404, detail="%s not found" % url_path)
   if url_path.endswith('/'):
      # Client explicitly requests this path as a directory.  Results are
      # relative to the reuqested path:
      html_content = Tools.html_dir_listing(client_url_path=url_path)
      return HTMLResponse(content=html_content, status_code=200)
   else:
      # full_fs_path is only used to check the type of path (file, dir):
      full_fs_path = Config.root_path + url_path
      logging.debug("Default Handler:  File System request full path: %s" % full_fs_path)
      if os.path.isdir(full_fs_path):
         # This is still a directory request, but we need to include client
         # request path in response:
         html_content = Tools.html_dir_listing(client_url_path=url_path)
         return HTMLResponse(content=html_content, status_code=200)
      else:
         if not os.path.exists(full_fs_path):
            logging.warning("Default Handler: Path not found: %s" % url_path)
            raise HTTPException(status_code=404, detail="%s not found" % \
                                url_path)
         response = FileResponse(path=full_fs_path)
         if some_path.endswith("/info"):
            response.headers["content-type"] = "application/octet-stream"
         if some_path.endswith(".txt"):
            response.headers["content-type"] = "application/text"
         return response


@router.delete("/{dataset_path}/annotations/{block}/{leaf_file}.json",
               description="Delete the annotation identified by 'id' in the JSON annotation file.",
               response_description="The 'id' of the deleted annotation.",
               response_model=models.AnnotationId
              )
async def delete_annotation(request: Request, dataset_path: str, block: str,
                      leaf_file: str, id: Optional[str] = None)->models.AnnotationId:
   '''
   Delete a single annotation in a JSON file.
   NOTE: This currently only works with AXON data.
   Args:
      request (Request): The client's REST request object.
      dataset_path (str): The top-level path the identifies a dataset.
      block (str): The encoded intermediate block path (xNNNyMMMzPPP).
      leaf_file (str): The json file name that contains the annotation to delete.
      id (Optional[str]): The id of the annotation to delete.
   Returns:
      str:  The id of the deleted annotation.
   Raises:
      HTTPException: If the annotation file was not found.
   '''
   response = Response()
   # Short-circuit:
   if id is None:
      logging.info("Delete Annotation: No 'id' provided to delete, so doing nothing.")
      response.status_code = 204 # 204 = No content
      return

   # Construct the local/full path from the request:
   # Translate block path:
   block_path = Tools.translate_block_path( \
                  "%s/annotations/block_info.csv" % dataset_path,
                  block)
   req_path = dataset_path + "/annotations/" + block_path + "/" + \
             leaf_file + ".json"
   full_path = Config.root_path + "/" + req_path
   logging.debug("Delete Annotation: Path %s maps to %s" % (req_path,full_path))
   logging.debug("Delete Annotation: Id to delete: %s" % str(id))
   if not os.path.exists(full_path):
      logging.warning("Delete Annotation: Path not found: %s.json" % leaf_file)
      raise HTTPException(status_code=404, detail="%s.json not found" % \
                          leaf_file)
   data = None
   res = []
   with open(full_path, 'r') as f:
      data = json.load(f)
   if data is not None:
       # Loop through dictionaries, adding ones that don't match 'id':
      for item in data:
         if item['id'] != id:
            res.append(item)
         else:
            logging.debug("Delete Annotation: Deleted item: %s" % id)
   jres = json.dumps(res, indent=3)
   with open(full_path, 'wb') as f:
      f.write(jres)
   response.status_code = 200
   return id


@router.patch("/{dataset_path}/annotations/{block}/{leaf_file}.json",
              description="Update an annotation in the specified JSON annotation file.",
              response_description="The 'id' of the updated annotation.",
              response_model=models.AnnotationId
             )
async def update_annotation(request: Request, dataset_path: str, block: str,
                      leaf_file: str, id: Optional[str] = None)->models.AnnotationId:
   '''
   Update a single annotation in a JSON file.
   NOTE: This currently only works with AXON data.
   Args:
      request (Request): The client's REST request object.  It contains the
                         information of the annotation to update.
      dataset_path (str): The top-level path the identifies a dataset.
      block (str): The encoded intermediate block path (xNNNyMMMzPPP).
      leaf_file (str): The json file name that contains the annotation to update.
      id (Optional[str]): The id of the annotation to update.
   Returns:
      str:  The id of the updated annotation.
   Raises:
      HTTPException: If the annotation file was not found.
   '''
   response = Response()
   block_path = Tools.translate_block_path( \
                  "%s/annotations/block_info.csv" % dataset_path,
                  block)
   req_path = dataset_path + "/annotations/" + block_path + "/" + \
             leaf_file + ".json"
   full_path = Config.root_path + "/" + req_path
   logging.info("Patch Annotation: Path %s maps to %s" % (req_path,full_path))
   if not os.path.exists(full_path):
      logging.warning("Patch Annotation: Path does not exist: %s.json" % leaf_file)
      raise HTTPException(status_code=404, detail="%s.json not found" % \
                          leaf_file)
   # Load the json payload:
   json_payload = b''
   async for chunk in request.stream():
      json_payload += chunk
   # Convert the json payload into python structure:
   json_data = json.loads(json_payload)
   json_id = json_data["id"]
   file_data = None
   res = []
   with open(full_path, 'r') as f:
      file_data = json.load(f)
   replaced = False
   if file_data is not None:
       # Loop through dictionaries, adding ones that don't match 'id':
      for item in file_data:
         if item['id'] == json_id:
            logging.debug("Patch Annotation: Replacing item: %s" % json_id)
            res.append(json_data)
            replaced = True
         else:
            res.append(item)
   if not replaced:
      logging.info("Patch Annotation: Item not replaced but added: %s" % json_id)
      res.append(json_data)
   jres = json.dumps(res, indent=3)
   with open(full_path, 'wb') as f:
      f.write(jres)
   response.status_code = 200
   return id


# Initialize the main application.
#
app = FastAPI(
         docs_url="/api/"
      )
app.include_router(router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600)
app.add_middleware(
    GZipMiddleware,
    minimum_size=512)
