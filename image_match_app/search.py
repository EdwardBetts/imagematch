import sys, os, shutil
from models import Image, QueryImage
from django.conf import settings as djsettings
import settings
import datetime
import sysv_ipc as ipc
import struct
import threading
import PIL.Image
import subprocess
import re

_cacheLock = threading.Lock()
_imageCache = {}

def get_shm(imageObj):
    """
    image: Image or QueryImage object
    """
    with _cacheLock:
        if imageObj not in _imageCache:
            image = PIL.Image.open(imageObj.path)
            w, h = image.size
            size = 8 + w * h * 3
            shm = ipc.SharedMemory(None, flags=ipc.IPC_CREX, size=size)
            if settings.SCALE_IMAGE:
                rateW = settings.SCALE_TO_LENGTH / float(w)
                rateH = settings.SCALE_TO_LENGTH / float(h)
                rate = min(1, rateW, rateH)
                w = int(w * rate)
                h = int(h * rate)
                image = image.resize((w, h))
            shm.write(struct.pack('LL', w, h))
            shm.write(image.tobytes(), offset=8)
            _imageCache[imageObj] = shm
        else:
            shm = _imageCache[imageObj]
    return shm

def clear_cache():
    _imageCache.clear();

def get_likelyhood(targetImg, modelImg):
    shm1 = get_shm(targetImg)
    shm2 = get_shm(modelImg)
    p = subprocess.Popen(
            [settings.COMPARER_BIN, str(shm1.key), str(shm1.size), str(shm2.key), str(shm2.size)], 
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE
        )
    p.wait()
    if p.returncode != 0:
        # error occured
        errInfo = p.stderr.read()
        raise Exception("Error comparing [%s] and [%s]:\n  %s" % (
            targetImg.id, modelImg.id, errInfo))
    return float(p.stdout.read())

def debug_get_likelyhood(queryId, imgId):
    targetImg = QueryImage.objects.get(id=queryId)
    modelImg = Image.objects.get(id=imgId)

    shm1 = get_shm(targetImg)
    shm2 = get_shm(modelImg)

    bin = settings.COMPARER_BIN

    p = subprocess.Popen(
            ['gdb', bin, '--args', bin, str(shm1.key), str(shm1.size), str(shm2.key), str(shm2.size)], 
        )
    p.wait()

def search(targetImg, number=100):
    """
    image: models.QueryImage object
    return: a list of matched results, with type models.Image .
    """
    results = []
    lock = threading.Semaphore(settings.WORKER_THREAD_COUNT)

    def add(image):
        with lock:
            results.append((get_likelyhood(targetImg, image), image))

    threads = []
    for image in Image.objects.all():
        with lock:
            thread = threading.Thread(target=add, args=(image,))
            thread.start()
            threads.append(thread)
    for thread in threads:
        thread.join()
    results.sort(key=lambda (r, img): -r)
    results = results[:number]
    return results

def import_image(src_path, dst_path):
    # copy to storePath
    shutil.copy(src_path, dst_path)
    image = Image(path=dst_path)
    image.save()
    return image
