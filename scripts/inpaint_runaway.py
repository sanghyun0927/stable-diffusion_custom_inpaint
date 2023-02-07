import argparse, os, sys, glob
from omegaconf import OmegaConf
from PIL import Image
from tqdm import tqdm
import numpy as np
import torch
import sys
sys.path.insert(
   1, "/data01/lorenzo.stacchio/TU GRAZ/Stable_Diffusion_Inpaiting/stable-diffusion_custom_inpaint")
from main import instantiate_from_config
from ldm.models.diffusion.ddim import DDIMSampler


def make_batch(image, mask, texture, device):
    image = np.array(Image.open(image).convert("RGB"))
    image = image.astype(np.float32)/255.0
    image = image[None].transpose(0,3,1,2)
    image = torch.from_numpy(image)

    texture_image = np.array(Image.open(texture).convert("RGB"))
    texture_image = texture_image.astype(np.float32)/255.0
    texture_image = texture_image[None].transpose(0,3,1,2)
    texture_image = torch.from_numpy(texture_image)

    mask = np.array(Image.open(mask).convert("L"))
    mask = mask.astype(np.float32)/255.0
    mask = mask[None,None]
    mask[mask < 0.5] = 0
    mask[mask >= 0.5] = 1
    mask = torch.from_numpy(mask)

    masked_image = (1-mask)*image

    batch = {"image": image, "mask": mask, "masked_image": masked_image, "texture":texture_image}

    for k in batch:
        batch[k] = batch[k].to(device=device)
        batch[k] = batch[k]*2.0-1.0
    return batch


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--indir",
        type=str,
        nargs="?",
        help="dir containing image-mask pairs (`example.png` and `example_mask.png`)",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        nargs="?",
        help="dir to write results to",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=50,
        help="number of ddim sampling steps",
    )

    opt = parser.parse_args()

    masks = sorted(glob.glob(os.path.join(opt.indir, "*_mask.png")))
    images = [x.replace("_mask.png", ".png") for x in masks]
    print(f"Found {len(masks)} inputs.")

    # config = OmegaConf.load("models/ldm/inpainting_big/config.yaml")
    # config = OmegaConf.load("configs/latent-diffusion/inpainting_runaway4_NOCLIP.yaml")
    config = OmegaConf.load("configs/latent-diffusion/inpainting_runaway.yaml")
    print(config)
    model = instantiate_from_config(config.model)
    print("Loadin modeling")
    # model.load_state_dict(torch.load("models/ldm/inpainting_big/last.ckpt")["state_dict"],
    #                        strict=False)

    model.load_state_dict(torch.load("models/ldm/inpainting_big/model_compvis.ckpt")["state_dict"],
                           strict=False)
    # model.load_state_dict(torch.load("models/ldm/inpainting_big/sd-v1-5-inpainting.ckpt")["state_dict"],
    #                        strict=False)
    print("Model loaded")
    # exit()

    #model = torch.load("models/ldm/inpainting_big/archive/data.pkl")
    
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    model = model.to(device)
    # print(type(model))
    # exit()

    sampler = DDIMSampler(model)

    os.makedirs(opt.outdir, exist_ok=True)
    with torch.no_grad():
        with model.ema_scope():
            for image, mask in tqdm(zip(images, masks)):
                texture = "data/INPAINTING/custom_inpainting/texture_background.png"
                prefix = os.path.basename(texture).split(".")[0]
                outpath = os.path.join(opt.outdir, prefix + os.path.split(image)[1])

                batch = make_batch(image, mask, texture, device=device)
                
                # encode masked image and concat downsampled mask
                c = model.cond_stage_model.encode(batch["masked_image"])
                # print(batch["masked_image"].shape)
                # print("\nSHAPE OF MASKED IMAGE ENCODED", c.shape)
                
                cc = torch.nn.functional.interpolate(batch["mask"],
                                                     size=c.shape[-2:])

                c = torch.cat((c, cc), dim=1)

                # print("SHAPE FED TO INPUT", c.shape)
                # shape = (c.shape[1]-2,)+c.shape[2:]
                shape = (3,) + c.shape[2:]

                # print("SHAPE FED TO INPUT", shape)

                # print("\nSAMPLING\n")
                
                samples_ddim, _ = sampler.sample(S=opt.steps,
                                                 conditioning=c,
                                                 batch_size=c.shape[0],
                                                 shape=shape,
                                                 verbose=False)
                # print(samples_ddim.shape)
                # exit()

                x_samples_ddim = model.decode_first_stage(samples_ddim)

                image = torch.clamp((batch["image"]+1.0)/2.0,
                                    min=0.0, max=1.0)
                mask = torch.clamp((batch["mask"]+1.0)/2.0,
                                   min=0.0, max=1.0)
                predicted_image = torch.clamp((x_samples_ddim+1.0)/2.0,
                                              min=0.0, max=1.0)

                inpainted = (1-mask)*image+mask*predicted_image
                inpainted = inpainted.cpu().numpy().transpose(0,2,3,1)[0]*255
                Image.fromarray(inpainted.astype(np.uint8)).save(outpath)