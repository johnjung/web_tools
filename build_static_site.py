#!/usr/bin/env python
"""Usage: build_static_site.py --webroot <webroot> --output-dir <output-dir>
          build_static_site.py --webroot <webroot> --rebuild-photo-albums
"""

import boto3
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import sys
import time
import urllib.request

from docopt import docopt
from PIL import Image, ExifTags

THUMBS = 'thumbnails'
FULLSIZE = 'fullsize'

def resize_s3_photos(aws_access_key_id, aws_secret_access_key, bucketname, photoalbum_dir):
    # resize photos from an S3 bucket for the PHP version of the site.
    boto_session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )
    s3 = boto3.resource(
        service_name='s3',
        region_name='us-east-2',
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    for obj_summary in s3.Bucket(bucketname).objects.all():
        print(obj_summary.key)
        obj = s3.Bucket(bucketname).Object(obj_summary.key).get()
        product_dir = obj_summary.key.split('/')[0]

        # be sure file is JPEG or PNG.
        if not obj_summary.key.split('.')[-1].upper() in ('JPG', 'PNG'):
            continue

        # create product/THUMBS and product/FULLSIZE directories, if necessary.
        for d in (
            os.path.join(photoalbum_dir, product_dir, THUMBS),
            os.path.join(photoalbum_dir, product_dir, FULLSIZE)
        ):
            if not os.path.isdir(d):
                os.makedirs(d)
                os.chmod(d, 0o775)

        # read the image into memory.
        with Image.open(obj['Body']) as img:
            # rotate, if necessary.
            exif = img._getexif()
            try:
                if exif[274] == 3:
                    img = img.rotate(180, expand=True)
                elif exif[274] == 6:
                    img = img.rotate(270, expand=True)
                elif exif[274] == 8:
                    img = img.rotate(90, expand=True)
            except (KeyError, TypeError):
                pass

            img_thumb = img.copy()

            # crop and save thumbnail.
            crop_size = min(img_thumb.size)
            img_thumb = img_thumb.crop(((img_thumb.size[0] - crop_size) // 2,
                      (img_thumb.size[1] - crop_size) // 2,
                      (img_thumb.size[0] + crop_size) // 2,
                      (img_thumb.size[1] + crop_size) // 2))
            img_thumb = img_thumb.resize((72, 72), Image.ANTIALIAS)
            img_thumb.save(
                os.path.join(
                    photoalbum_dir,
                    product_dir,
                    THUMBS,
                    '{}.jpg'.format(os.path.splitext(obj_summary.key.split('/')[1])[0])
                ),
                'JPEG'
            )

            # crop and save fullsized image.
            # get scaling factor so x is no greater than 975 and y is no
            # greater than 500.
            s = min((975.0 / img.size[0], 500.0 / img.size[1]))
            img = img.resize((int(img.size[0] * s), int(img.size[1] * s)), Image.ANTIALIAS)

            img.save(
                os.path.join(
                    photoalbum_dir,
                    product_dir,
                    FULLSIZE,
                    '{}.jpg'.format(os.path.splitext(obj_summary.key.split('/')[1])[0])
                ),
                'JPEG'
            )


if __name__=='__main__':
    arguments = docopt(__doc__)

    # save cwd.
    cwd = os.getcwd()

    # absolute path to webroot.
    webroot_abs = os.path.abspath(arguments['<webroot>'])
    if webroot_abs.endswith('/'):
        webroot_abs = webroot_abs[:-1]

    # absolute path to the photo album.
    photoalbum_abs = os.path.join(
        webroot_abs,
        'images',
        'photoalbum'
    )

    # tempdir location to place photoalbum dir, if it already exists.
    photoalbum_dir_temp = '/tmp/photoalbum.{}'.format(
        time.time()
    )

    if arguments['--rebuild-photo-albums']:
        # if /images/photoalbum/ exists, move it out of the way.
        if os.path.isdir(photoalbum_abs):
            shutil.move(
                photoalbum_abs,
                photoalbum_dir_temp
            )

        # make a new photoalbum dir.
        os.mkdir(photoalbum_abs)
  
        # resize S3 images for static site.
        resize_s3_photos(
            os.environ['S3_ACCESS_KEY_ID'],
            os.environ['S3_SECRET_ACCESS_KEY'],
            'chesterfieldawning.photos',
            photoalbum_abs
        )
        sys.exit()

    # absolute path to output directory.
    output_dir_abs = os.path.abspath(arguments['<output-dir>'])
    if output_dir_abs.endswith('/'):
        output_dir_abs = output_dir_abs[:-1]

    # basename of the output directory.
    output_dir_name = os.path.basename(output_dir_abs)

    # tempdir location to place output dir, if it already exists.
    output_dir_temp = '/tmp/{}.{}'.format(
        output_dir_name,
        time.time()
    )

    # if the output directory exists, move it into /tmp/. 
    if os.path.isdir(output_dir_abs):
        shutil.move(
            output_dir_abs,
            output_dir_temp
        )

    # make the output directory.
    os.mkdir(output_dir_abs)

    # copy static files as-is.
    for f in ('favicon.ico',):
        shutil.copyfile(
            os.path.join(webroot_abs, f),
            os.path.join(output_dir_abs, f)
        )

    # copy static directories as-is. 
    for d in ('css', 'images', 'js', 'pdf'):
        shutil.copytree(
            os.path.join(webroot_abs, d),
            os.path.join(output_dir_abs, d)
        )

    # change cwd to the webroot.
    os.chdir(webroot_abs)

    # start a PHP webserver.

    print('starting webserver')

    p = subprocess.Popen(
        [
            'php',
            '-S',
            'localhost:8000'
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
 
    time.sleep(5)

    # render files to HTML and save.
    for root, dirs, files in os.walk(webroot_abs):
        if root.endswith('/includes'):
            continue
        for file in files:
            if file.endswith('.php'):
                webroot_path = os.path.join(root, file)
                server_path = webroot_path.replace(webroot_abs, 'http://localhost:8000')
                server_path = re.sub('index\.php$', '', server_path)
                output_path = webroot_path.replace(webroot_abs, output_dir_abs)
                output_path = re.sub('\.php$', '.html', output_path)

                print(server_path)

                # create directories.
                try:
                    os.makedirs(os.path.dirname(output_path))
                except FileExistsError:
                    pass

                with urllib.request.urlopen(server_path) as f_in, open(output_path, 'w') as f_out:
                    f_out.write(f_in.read().decode('utf-8'))

    # change back to the current working directory.
    os.chdir(cwd)

    # terminate PHP webserver subprocess.
    p.terminate()
