import argparse
import cv2
import os
import h5py
import torch
import numpy as np

from os import listdir
from torch.autograd import Variable
from tqdm import tqdm
from data_utils import is_image_file
from model import NetE, NetME
from psnr import psnr
from mse import mse
from PIL import Image

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Test NetM PyTorch')
    parser.add_argument('--modelE_name', default='model_epoch_800.pth', type=str, help='NetE model name')
    parser.add_argument('--modelM_name', default='model_best.pth', type=str, help='NetM model name')
    parser.add_argument('--crop_height', default=64, type=int, help='crop height')
    parser.add_argument('--crop_width', default=64, type=int, help='crop width')
    parser.add_argument('--sample_rate', default=0.2, type=int, help='sample_rate')
    parser.add_argument('--nef', default=64, type=int, help='number of encoder filters in first conv layer')
    opt = parser.parse_args()
    
    print('===> Select GPU to TEST...') 
    os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"   
    os.environ["CUDA_VISIBLE_DEVICES"]="0"
    
##  ================================================================================   
##  Test 100 images from h5 file
    path = 'data_val_100.h5'
    image_h5_file = h5py.File(path, 'r')
    image_dataset = image_h5_file['data'].value
    
    data_mask_image = np.zeros(image_dataset.shape, dtype=float)
    data_mask_weight = np.zeros((image_dataset.shape[0], 1, image_dataset.shape[2], image_dataset.shape[3]), dtype=float)
    data_real_frame = image_dataset.copy()
    data_restored_frame = np.zeros(image_dataset.shape, dtype=float)
    
    image_dataset = (image_dataset + 1.0) / 2.0
    image_dataset[:,0,:,:] = (image_dataset[:,0,:,:] - 0.5) / (0.5)
    image_dataset[:,1,:,:] = (image_dataset[:,1,:,:] - 0.5) / (0.5)
    image_dataset[:,2,:,:] = (image_dataset[:,2,:,:] - 0.5) / (0.5)
    
    print('===> Loading NetE model...')
    modelE = NetE(nef = opt.nef)
    if torch.cuda.is_available():
        modelE = modelE.cuda()
    modelE = torch.load('epochs_NetE/' + opt.modelE_name)
    modelE.eval()
    print(modelE)
    
    print('===> Loading NetME model...')
    modelME = NetME(nef = opt.nef, NetE_name = 'epochs_NetE/' + opt.modelE_name, sample_rate = opt.sample_rate)
    if torch.cuda.is_available():
        modelME = modelME.cuda()
    modelME.load_state_dict(torch.load('epochs_NetME/' + opt.modelME_name))
    modelME.eval()
    modelME.netM.eval()
    modelME.netE.eval()
    print(modelME)
    
    modelM = modelME.netM
    modelM.eval()
    print(modelM)
    
    out_path = 'results/netM_results/'
    if not os.path.exists(out_path):
        os.makedirs(out_path)
        
    netE_rand_PSNR = np.zeros((image_dataset.shape[0], 1))
    netE_adap_PSNR = np.zeros((image_dataset.shape[0], 1))
    netE_rand_MSE  = np.zeros((image_dataset.shape[0], 1))
    netE_adap_MSE  = np.zeros((image_dataset.shape[0], 1))

    rand_corrupt_PSNR = np.zeros((image_dataset.shape[0], 1))
    adap_corrupt_PSNR = np.zeros((image_dataset.shape[0], 1))
    rand_corrupt_MSE  = np.zeros((image_dataset.shape[0], 1))
    adap_corrupt_MSE  = np.zeros((image_dataset.shape[0], 1))
    
    criterion = torch.nn.MSELoss()
    
    img_idx = 0
    
    for index in tqdm(range(0,image_dataset.shape[0]), desc='sampling-reconstruction on rand/adap corrupted images'):

        targetRGB = image_dataset[index]
        
        channel, width, height = targetRGB.shape
        targetRGB = np.asarray(targetRGB)
        image_size = targetRGB.shape

        targetRGB_scale = targetRGB.copy()
 
        #   Resize to 1 x C x W x H
        targetRGB_scale_4d = targetRGB_scale.reshape(1, 3, targetRGB_scale.shape[1], targetRGB_scale.shape[2])
        
        imageRGB_scale_4d = targetRGB_scale_4d.copy()
        
        #   Transfer to Torch Variable
        targetRGB_scale_4d = Variable(torch.from_numpy(targetRGB_scale_4d))
        imageRGB_scale_4d  = Variable(torch.from_numpy(imageRGB_scale_4d))
        
        #   Generate the random corruption mask
        corrupt_mask_rand_4d = torch.ones(imageRGB_scale_4d.shape[0], 1, imageRGB_scale_4d.shape[2], imageRGB_scale_4d.shape[3])
        corrupt_mask_rand_4d = corrupt_mask_rand_4d * opt.sample_rate
        corrupt_mask_rand_4d = corrupt_mask_rand_4d.bernoulli()
        corrupt_mask_rand_4d = corrupt_mask_rand_4d.expand(corrupt_mask_rand_4d.shape[0], 3, corrupt_mask_rand_4d.shape[2], corrupt_mask_rand_4d.shape[3])
        
        corrupt_image_scale_rand_4d = corrupt_mask_rand_4d * imageRGB_scale_4d
        
        #   Generate the adaptive corruption mask
        targetRGB_scale_4d = targetRGB_scale_4d.cuda()
        corrupt_mask_adap_4d = modelM(targetRGB_scale_4d)
        
        #   Rescale sampling rate to be exact opt.sample_rate
        corrupt_mask_adap_4d_size = corrupt_mask_adap_4d.size()
        corrupt_mask_adap_4d_mean = torch.mean(corrupt_mask_adap_4d, 2, True)
        corrupt_mask_adap_4d_mean = torch.mean(corrupt_mask_adap_4d_mean, 3, True)
        corrupt_mask_adap_4d_mean = corrupt_mask_adap_4d_mean.expand(corrupt_mask_adap_4d_size[0], corrupt_mask_adap_4d_size[1], corrupt_mask_adap_4d_size[2], corrupt_mask_adap_4d_size[3])
        
        corrupt_mask_adap_4d = corrupt_mask_adap_4d / corrupt_mask_adap_4d_mean * opt.sample_rate
        corrupt_mask_adap_weight_4d = corrupt_mask_adap_4d.cpu()
        
        corrupt_mask_adap_4d = corrupt_mask_adap_4d.bernoulli()        
        corrupt_mask_adap_4d = corrupt_mask_adap_4d.cpu()
        corrupt_mask_adap_4d = corrupt_mask_adap_4d.expand(corrupt_mask_adap_4d.shape[0], 3, corrupt_mask_adap_4d.shape[2], corrupt_mask_adap_4d.shape[3])
        
        corrupt_image_scale_adap_4d = corrupt_mask_adap_4d * imageRGB_scale_4d
        
        corrupt_image_scale_rand_4d = corrupt_image_scale_rand_4d.cuda()
        corrupt_image_scale_adap_4d = corrupt_image_scale_adap_4d.cuda()
        out_rand = modelE(corrupt_image_scale_rand_4d)
        out_adap = modelE(corrupt_image_scale_adap_4d)
        
        #   Transfer the image back to numpy and cpu
        out_rand = out_rand.cpu()
        out_adap = out_adap.cpu()
        imageRGB_scale_rand_recon = out_rand.data[0].numpy()
        imageRGB_scale_adap_recon = out_adap.data[0].numpy()
        corrupt_image_scale_rand_4d = corrupt_image_scale_rand_4d.cpu()
        corrupt_image_scale_adap_4d = corrupt_image_scale_adap_4d.cpu()
        corrupt_image_scale_rand  = corrupt_image_scale_rand_4d.data[0].numpy()
        corrupt_image_scale_adap  = corrupt_image_scale_adap_4d.data[0].numpy()
        
        corrupt_mask_adap = corrupt_mask_adap_4d.data[0].numpy()
        corrupt_mask_rand  = corrupt_mask_rand_4d.data[0].numpy()
        corrupt_mask_adap_weight = corrupt_mask_adap_weight_4d.data[0].numpy()
        
        imageRGB_rand_recon = imageRGB_scale_rand_recon.copy()
        imageRGB_adap_recon = imageRGB_scale_adap_recon.copy()
        corrupt_image_rand  = corrupt_image_scale_rand.copy()
        corrupt_image_adap  = corrupt_image_scale_adap.copy()
        
        data_mask_image[index,:,:,:]     = corrupt_mask_adap.copy()
        data_mask_weight[index,:,:,:]    = corrupt_mask_adap_weight.copy()
        data_restored_frame[index,:,:,:] = imageRGB_adap_recon.copy()
        
        imageRGB_rand_recon[0,:,:] = (imageRGB_rand_recon[0,:,:] * 0.5) + 0.5
        imageRGB_rand_recon[1,:,:] = (imageRGB_rand_recon[1,:,:] * 0.5) + 0.5
        imageRGB_rand_recon[2,:,:] = (imageRGB_rand_recon[2,:,:] * 0.5) + 0.5
        
        imageRGB_adap_recon[0,:,:] = (imageRGB_adap_recon[0,:,:] * 0.5) + 0.5
        imageRGB_adap_recon[1,:,:] = (imageRGB_adap_recon[1,:,:] * 0.5) + 0.5
        imageRGB_adap_recon[2,:,:] = (imageRGB_adap_recon[2,:,:] * 0.5) + 0.5
        
        corrupt_image_rand[0,:,:] = (corrupt_image_rand[0,:,:] * 0.5) + 0.5
        corrupt_image_rand[1,:,:] = (corrupt_image_rand[1,:,:] * 0.5) + 0.5
        corrupt_image_rand[2,:,:] = (corrupt_image_rand[2,:,:] * 0.5) + 0.5
        
        corrupt_image_adap[0,:,:] = (corrupt_image_adap[0,:,:] * 0.5) + 0.5
        corrupt_image_adap[1,:,:] = (corrupt_image_adap[1,:,:] * 0.5) + 0.5
        corrupt_image_adap[2,:,:] = (corrupt_image_adap[2,:,:] * 0.5) + 0.5
        
        targetRGB[0,:,:] = (targetRGB[0,:,:] * 0.5) + 0.5
        targetRGB[1,:,:] = (targetRGB[1,:,:] * 0.5) + 0.5
        targetRGB[2,:,:] = (targetRGB[2,:,:] * 0.5) + 0.5
        
        imageRGB_rand_recon[imageRGB_rand_recon>1] = 1
        imageRGB_adap_recon[imageRGB_adap_recon>1] = 1
        corrupt_image_rand[corrupt_image_rand>1] = 1
        corrupt_image_adap[corrupt_image_adap>1] = 1
      
        imageRGB_rand_recon[imageRGB_rand_recon<0] = 0
        imageRGB_adap_recon[imageRGB_adap_recon<0] = 0
        corrupt_image_rand[corrupt_image_rand<0] = 0
        corrupt_image_adap[corrupt_image_adap<0] = 0

        #   Compute Stat here
        netE_rand_PSNR[img_idx] = psnr((targetRGB*255.0).astype(int), (imageRGB_rand_recon*255.0).astype(int))
        netE_adap_PSNR[img_idx] = psnr((targetRGB*255.0).astype(int), (imageRGB_adap_recon*255.0).astype(int))
        
        netE_rand_MSE[img_idx] = mse((targetRGB*255.0).astype(int), (imageRGB_rand_recon*255.0).astype(int))
        netE_adap_MSE[img_idx] = mse((targetRGB*255.0).astype(int), (imageRGB_adap_recon*255.0).astype(int))

        rand_corrupt_PSNR[img_idx] = psnr((targetRGB*255.0).astype(int), (corrupt_image_rand*255.0).astype(int))
        adap_corrupt_PSNR[img_idx] = psnr((targetRGB*255.0).astype(int), (corrupt_image_adap*255.0).astype(int))
        
        rand_corrupt_MSE[img_idx] = mse((targetRGB*255.0).astype(int), (corrupt_image_rand*255.0).astype(int))
        adap_corrupt_MSE[img_idx] = mse((targetRGB*255.0).astype(int), (corrupt_image_adap*255.0).astype(int))
        
        #   Write to Imgage
        imageRGB_rand_recon *= 255.0
        imageRGB_adap_recon *= 255.0
        corrupt_image_rand  *= 255.0
        corrupt_image_adap  *= 255.0
        
        targetRGB *= 255.0
        
        imageRGB_rand_recon = np.transpose(imageRGB_rand_recon, (1, 2, 0))
        imageRGB_adap_recon = np.transpose(imageRGB_adap_recon, (1, 2, 0))
        corrupt_image_rand  = np.transpose(corrupt_image_rand,  (1, 2, 0))
        corrupt_image_adap  = np.transpose(corrupt_image_adap,  (1, 2, 0))
        
        targetRGB = np.transpose(targetRGB, (1, 2, 0))
        
        image_name_base = "img_"
        cv2.imwrite(out_path + image_name_base + str(index) + '_rand_recon.png',   cv2.cvtColor(imageRGB_rand_recon.astype(np.uint8), cv2.COLOR_RGB2BGR))
        cv2.imwrite(out_path + image_name_base + str(index) + '_adap_recon.png',   cv2.cvtColor(imageRGB_adap_recon.astype(np.uint8), cv2.COLOR_RGB2BGR))
        cv2.imwrite(out_path + image_name_base + str(index) + '_rand_corrupt.png', cv2.cvtColor(corrupt_image_rand.astype(np.uint8), cv2.COLOR_RGB2BGR))
        cv2.imwrite(out_path + image_name_base + str(index) + '_adap_corrupt.png', cv2.cvtColor(corrupt_image_adap.astype(np.uint8), cv2.COLOR_RGB2BGR))
        cv2.imwrite(out_path + image_name_base + str(index) + '_gt.png', cv2.cvtColor(targetRGB.astype(np.uint8), cv2.COLOR_RGB2BGR))
        img_idx += 1

        
    print("===> Test on BSD100 Complete: NET ADAP PSNR: {:.4f} dB, NET ADAP MSE: {:.4f}, NET RAND PSNR: {:.4f} dB, NET RAND MSE: {:.4f}"
          .format(np.average(netE_adap_PSNR), np.average(netE_adap_MSE), np.average(netE_rand_PSNR), np.average(netE_rand_MSE)))

    print("===> Test on BSD100 Complete: COR ADAP PSNR: {:.4f} dB, COR ADAP MSE: {:.4f}, COR RAND PSNR: {:.4f} dB, COR RAND MSE: {:.4f}"
          .format(np.average(adap_corrupt_PSNR), np.average(adap_corrupt_MSE), np.average(rand_corrupt_PSNR), np.average(rand_corrupt_MSE)))
    
    #   Save Results to H5 file     
    h5_file_name = out_path + 'netE_adap_PSNR.h5'

    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=netE_adap_PSNR)
        
    h5_file_name = out_path + 'netE_rand_PSNR.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=netE_rand_PSNR)
    
    h5_file_name = out_path + 'netE_adap_MSE.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=netE_adap_MSE)
        
    h5_file_name = out_path + 'netE_rand_MSE.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=netE_rand_MSE)
    
    h5_file_name = out_path + 'corrupt_adap_PSNR.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=adap_corrupt_PSNR)
        
    h5_file_name = out_path + 'corrupt_rand_PSNR.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=rand_corrupt_PSNR)
    
    h5_file_name = out_path + 'corrupt_adap_MSE.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=adap_corrupt_MSE)
        
    h5_file_name = out_path + 'corrupt_rand_MSE.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data",  data=rand_corrupt_MSE)
        
    h5_file_name = out_path + 'res_net_clip_mean_iter_ber_conti_sig_M_fix_E.h5'
    with h5py.File(h5_file_name, 'w') as hf:
        hf.create_dataset("data_mask_image",  data=data_mask_image)
        hf.create_dataset("data_mask_weight",  data=data_mask_weight)
        hf.create_dataset("data_real_frame",  data=data_real_frame)
        hf.create_dataset("data_restored_frame",  data=data_restored_frame)
