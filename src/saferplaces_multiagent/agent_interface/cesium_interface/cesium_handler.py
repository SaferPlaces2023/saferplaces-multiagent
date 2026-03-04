import os

from ...common import utils, s3_utils

from .wd3d_preprocessor import WD3dMeshPreprocessor



class CesiumHandler:
    
    def __init__(self, user_id, project_id):
        self.user_id = user_id
        self.project_id = project_id
        self.wd_preprocessor = WD3dMeshPreprocessor(verbose=True)

    def preprocess_wd(self, wd_tif_uri, wd_cbor_uri = None, force=False):
        wd_cbor_uri = wd_tif_uri.rsplit('/', 1)[0] + '/' + os.path.basename(wd_tif_uri).replace('.tif', '.cbor') if wd_cbor_uri is None else wd_cbor_uri
        if s3_utils.s3_exists(wd_cbor_uri) and not force:
            print(f"WD CBOR file already exists: {wd_cbor_uri}")
            return wd_cbor_uri
        
        wd_tif_filename = os.path.join(utils._temp_dir, utils.justfname(wd_tif_uri))
        wd_tif_filename = s3_utils.s3_download(wd_tif_uri, wd_tif_filename)
        
        if wd_tif_filename is None:
            print('Failed to download WD file from S3.')
            return None
        
        wd_cbor_filename = self.wd_preprocessor.run_pipeline(wd_tif_filename)
        if wd_cbor_filename is None:
            print('Failed to preprocess WD file.')
            return None
        
        s3_utils.s3_upload(wd_cbor_filename, wd_cbor_uri)

        if not s3_utils.s3_exists(wd_cbor_uri):
            print("Failed to upload WD CBOR file to S3.")
            return None

        return wd_cbor_uri