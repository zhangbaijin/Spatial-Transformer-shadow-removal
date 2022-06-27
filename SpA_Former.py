import torch
from torch import nn
import torch.nn.functional as F
from collections import OrderedDict
from models.models_utils import weights_init, print_network
# import common
from Dual_conv import PaddingConv2d
from TransFormer import TransformerBlock,OverlapPatchEmbed,Downsample,Upsample



###### Layer 
def conv1x1(in_channels, out_channels, stride = 1):
    return nn.Conv2d(in_channels,out_channels,kernel_size = 1,
                    stride =stride, padding=0,bias=False)

def conv3x3(in_channels, out_channels, stride = 1):
    return nn.Conv2d(in_channels,out_channels,kernel_size = 3,
        stride =stride, padding=1,bias=False)


'''    
class Bottleneck(nn.Module):
    def __init__(self,in_channels,out_channels,):
        super(Bottleneck,self).__init__()
        m  = OrderedDict()
        m['conv1'] = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=1, padding=2, bias=False,dilation=2)
        m['relu1'] = nn.ReLU(True)
        m['conv2'] = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=1, padding=2, bias=False,dilation=2)
        self.group1 = nn.Sequential(m)
        self.relu= nn.Sequential(nn.ReLU(True))

    def forward(self, x):
        out = self.group1(x) 
        return out

'''    
  
class ResBlock(nn.Module):
    def __init__(self, in_channel, out_channel):
        super(ResBlock, self).__init__()
        m  = OrderedDict()
        m['conv1'] = nn.Conv2d(in_channel, out_channel, kernel_size=3, stride=1, padding=1,bias=False)
        m['relu1'] = nn.ReLU(True)
        m['conv2'] = nn.Conv2d(in_channel, out_channel, kernel_size=3, stride=1, padding=1,bias=False)
        self.main = nn.Sequential(m)
        self.relu= nn.Sequential(nn.ReLU(True))
        
    def forward(self, x):
        return self.main(x)

class ResBlock_fft_bench(nn.Module):
    def __init__(self, in_channel, out_channel, norm='backward'): # 'ortho'
        super(ResBlock_fft_bench, self).__init__()
        m  = OrderedDict()
        m['conv1'] = nn.Conv2d(in_channel*2, out_channel*2, kernel_size=1, stride=1, bias=False)
        m['relu1'] = nn.ReLU(True)
        m['conv2'] = nn.Conv2d(in_channel*2, out_channel*2, kernel_size=1, stride=1, bias=False)
        self.main_fft = nn.Sequential(m)

        self.dim = out_channel
        self.norm = norm
    def forward(self, x):
        _, _, H, W = x.shape
        dim = 1
        y = torch.fft.rfft2(x, norm=self.norm)
        y_imag = y.imag
        y_real = y.real
        y_f = torch.cat([y_real, y_imag], dim=dim)
        y = self.main_fft(y_f)
        y_real, y_imag = torch.chunk(y, 2, dim=dim)
        y = torch.complex(y_real, y_imag)
        y = torch.fft.irfft2(y, s=(H, W), norm=self.norm)
        return  y
    
    
class irnn_layer(nn.Module):
    def __init__(self,in_channels):
        super(irnn_layer,self).__init__()
        self.left_weight = nn.Conv2d(in_channels,in_channels,kernel_size=1,stride=1,groups=in_channels,padding=0)
        self.right_weight = nn.Conv2d(in_channels,in_channels,kernel_size=1,stride=1,groups=in_channels,padding=0)
        self.up_weight = nn.Conv2d(in_channels,in_channels,kernel_size=1,stride=1,groups=in_channels,padding=0)
        self.down_weight = nn.Conv2d(in_channels,in_channels,kernel_size=1,stride=1,groups=in_channels,padding=0)
        
    def forward(self,x):
        _,_,H,W = x.shape
        top_left = x.clone()
        top_right = x.clone()
        top_up = x.clone()
        top_down = x.clone()
        top_left[:,:,:,1:] = F.relu(self.left_weight(x)[:,:,:,:W-1]+x[:,:,:,1:],inplace=False)
        top_right[:,:,:,:-1] = F.relu(self.right_weight(x)[:,:,:,1:]+x[:,:,:,:W-1],inplace=False)
        top_up[:,:,1:,:] = F.relu(self.up_weight(x)[:,:,:H-1,:]+x[:,:,1:,:],inplace=False)
        top_down[:,:,:-1,:] = F.relu(self.down_weight(x)[:,:,1:,:]+x[:,:,:H-1,:],inplace=False)
        return (top_up,top_right,top_down,top_left)


class Attention(nn.Module):
    def __init__(self,in_channels):
        super(Attention,self).__init__()
        self.out_channels = int(in_channels/2)
        self.conv1 = nn.Conv2d(in_channels,self.out_channels,kernel_size=3,padding=1,stride=1)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv2d(self.out_channels,self.out_channels,kernel_size=3,padding=1,stride=1)
        self.relu2 = nn.ReLU()
        self.conv3 = nn.Conv2d(self.out_channels,4,kernel_size=1,padding=0,stride=1)
        self.sigmod = nn.Sigmoid()
    
    def forward(self,x):
        out = self.conv1(x)
        out = self.relu1(out)
        out = self.conv2(out)
        out = self.relu2(out)
        out = self.conv3(out)
        out = self.sigmod(out)
        return out

class SAM(nn.Module):
    def __init__(self,in_channels,out_channels,attention=1):
        super(SAM,self).__init__()
        self.out_channels = out_channels
        self.irnn1 = irnn_layer(self.out_channels)
        self.irnn2 = irnn_layer(self.out_channels)
        self.conv_in = conv3x3(in_channels,self.out_channels)
        self.relu1 = nn.ReLU(True)
        
        self.conv1 = nn.Conv2d(self.out_channels,self.out_channels,kernel_size=1,stride=1,padding=0)
        self.conv2 = nn.Conv2d(self.out_channels*4,self.out_channels,kernel_size=1,stride=1,padding=0)
        self.conv3 = nn.Conv2d(self.out_channels*4,self.out_channels,kernel_size=1,stride=1,padding=0)
        self.relu2 = nn.ReLU(True)
        self.attention = attention
        if self.attention:
            self.attention_layer = Attention(in_channels)
        self.conv_out = conv1x1(self.out_channels,1)
        self.sigmod = nn.Sigmoid()
    
    def forward(self,x):
        if self.attention:
            weight = self.attention_layer(x)
        out = self.conv1(x)
        top_up,top_right,top_down,top_left = self.irnn1(out)
        
        # direction attention
        if self.attention:
            top_up.mul(weight[:,0:1,:,:])
            top_right.mul(weight[:,1:2,:,:])
            top_down.mul(weight[:,2:3,:,:])
            top_left.mul(weight[:,3:4,:,:])
        out = torch.cat([top_up,top_right,top_down,top_left],dim=1)
        out = self.conv2(out)
        top_up,top_right,top_down,top_left = self.irnn2(out)
        
        # direction attention
        if self.attention:
            top_up.mul(weight[:,0:1,:,:])
            top_right.mul(weight[:,1:2,:,:])
            top_down.mul(weight[:,2:3,:,:])
            top_left.mul(weight[:,3:4,:,:])
        
        out = torch.cat([top_up,top_right,top_down,top_left],dim=1)
        out = self.conv3(out)
        out = self.relu2(out)
        mask = self.sigmod(self.conv_out(out))
        return mask
       

###### Network
class SPANet(nn.Module):
    def __init__(self,
        inp_channels=3, 
        out_channels=3, 
        dim = 32,
        num_blocks = [4,6,6,8], 
        num_refinement_blocks = 4,
        heads = [1,2,4,8],
        ffn_expansion_factor = 2.66,
        bias = False,
        LayerNorm_type = 'WithBias',   ## Other option 'BiasFree'
        dual_pixel_task = False  ):
        
        super(SPANet,self).__init__()
        
        self.conv_in = nn.Sequential(
            conv3x3(3,32),
            nn.ReLU(True)
            )
        self.conv_in1 = nn.Sequential(
            conv3x3(32,32),
            nn.ReLU(True)
            )
        self.conv_in2 = nn.Sequential(
            conv3x3(32,32),
            nn.ReLU(True)
            )
        self.SAM1 = SAM(32,32,1)
        
        self.patch_embed = OverlapPatchEmbed(inp_channels, dim)

        self.encoder_level1 = nn.Sequential(*[TransformerBlock(dim=dim, num_heads=heads[0], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[0])])
        
        self.down1_2 = Downsample(dim) ## From Level 1 to Level 2
        
        '''
        self.encoder_level2 = nn.Sequential(*[TransformerBlock(dim=int(dim*2**1), num_heads=heads[1], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[1])])
        
        self.down2_3 = Downsample(int(dim*2**1)) ## From Level 2 to Level 3
        self.encoder_level3 = nn.Sequential(*[TransformerBlock(dim=int(dim*2**2), num_heads=heads[2], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[2])])
        
        self.down3_4 = Downsample(int(dim*2**2)) ## From Level 3 to Level 4
        '''
        self.latent = nn.Sequential(*[TransformerBlock(dim=int(dim*2**1), num_heads=heads[3], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[3])])
        '''
        self.up4_3 = Upsample(int(dim*2**3)) ## From Level 4 to Level 3
        self.reduce_chan_level3 = nn.Conv2d(int(dim*2**3), int(dim*2**2), kernel_size=1, bias=bias)
        self.decoder_level3 = nn.Sequential(*[TransformerBlock(dim=int(dim*2**2), num_heads=heads[2], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[2])])


        self.up3_2 = Upsample(int(dim*2**2)) ## From Level 3 to Level 2
        self.reduce_chan_level2 = nn.Conv2d(int(dim*2**2), int(dim*2**1), kernel_size=1, bias=bias)
        self.decoder_level2 = nn.Sequential(*[TransformerBlock(dim=int(dim*2**1), num_heads=heads[1], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[1])])
        '''
        
        self.up2_1 = Upsample(int(dim*2**1))  ## From Level 2 to Level 1  (NO 1x1 conv to reduce channels)
        self.reduce_chan_level1 = nn.Conv2d(int(dim*2**1), int(dim), kernel_size=1, bias=bias)
        self.decoder_level1 = nn.Sequential(*[TransformerBlock(dim=int(dim), num_heads=heads[0], ffn_expansion_factor=ffn_expansion_factor, bias=bias, LayerNorm_type=LayerNorm_type) for i in range(num_blocks[0])])
        

        self.res_block1 = ResBlock(32,32)
        self.res_block2 = ResBlock(32,32)
        self.res_block3 = ResBlock(32,32)
        self.res_block4 = ResBlock(32,32)
        self.res_block5 = ResBlock(32,32)
        self.res_block6 = ResBlock(32,32)
        self.res_block7 = ResBlock(32,32)
        self.res_block8 = ResBlock(32,32)
        self.res_block9 = ResBlock(32,32)
        self.res_block10 = ResBlock(32,32)
        self.res_block11 = ResBlock(32,32)
        self.res_block12 = ResBlock(32,32)
        self.res_block13 = ResBlock(32,32)
        self.res_block14 = ResBlock(32,32)
        self.res_block15 = ResBlock(32,32)
        self.res_block16 = ResBlock(32,32)
        self.res_block17 = ResBlock(32,32)
        self.conv_out = nn.Sequential(
            conv3x3(32,3)
        )
        self.fft_block1 = ResBlock_fft_bench(32,32)
        self.fft_block2 = ResBlock_fft_bench(32,32)
        self.fft_block3 = ResBlock_fft_bench(32,32)
        self.fft_block4 = ResBlock_fft_bench(32,32)
        self.fft_block5 = ResBlock_fft_bench(32,32)
        self.fft_block6 = ResBlock_fft_bench(32,32)
        self.fft_block7 = ResBlock_fft_bench(32,32)
        self.fft_block8 = ResBlock_fft_bench(32,32)
        self.fft_block9 = ResBlock_fft_bench(32,32)
        self.fft_block10 = ResBlock_fft_bench(32,32)
        self.fft_block11 = ResBlock_fft_bench(32,32)
        self.fft_block12 = ResBlock_fft_bench(32,32)
        self.fft_block13 = ResBlock_fft_bench(32,32)
        self.fft_block14 = ResBlock_fft_bench(32,32)
        self.fft_block15 = ResBlock_fft_bench(32,32)
        self.fft_block16 = ResBlock_fft_bench(32,32)
        self.fft_block17 = ResBlock_fft_bench(32,32)
        
        
    def forward(self, x):
        
        
        out1 = self.conv_in(x)
        out1 = self.conv_in1(out1)
        out1 = self.conv_in2(out1)
        
        
        inp_enc_level1 = self.patch_embed(x)
        out_enc_level1 = self.encoder_level1(inp_enc_level1)
        
        inp_enc_level2 = self.down1_2(out_enc_level1)        
        latent = self.latent(inp_enc_level2) 
                        
        inp_dec_level1 = self.up2_1(latent)
        inp_dec_level1 = torch.cat([inp_dec_level1, out_enc_level1], 1)
        inp_dec_level1 = self.reduce_chan_level1(inp_dec_level1)
        out_dec_level1 = self.decoder_level1(inp_dec_level1)
        
        
        
        out = out_dec_level1 + out1
        #out_dec_level1 = self.refinement(out_dec_level1)
        
        
        
        #out = self.conv_in(x)
        #out = self.conv_in1(out)
        #out = self.conv_in2(out)
        
        out = F.relu(self.res_block1(out) + out + self.fft_block1(out))
        out = F.relu(self.res_block2(out) + out + self.fft_block2(out))
        out = F.relu(self.res_block3(out) + out + self.fft_block3(out))
       
        Attention1= self.SAM1(out) 
        out = F.relu(self.res_block4(out) * Attention1  + out + self.fft_block4(out))
        out = F.relu(self.res_block5(out) * Attention1  + out + self.fft_block5(out))
        out = F.relu(self.res_block6(out) * Attention1  + out + self.fft_block6(out))
        
        Attention2 = self.SAM1(out) 
        out = F.relu(self.res_block7(out) * Attention2 + out + self.fft_block7(out))
        out = F.relu(self.res_block8(out) * Attention2 + out + self.fft_block8(out))
        out = F.relu(self.res_block9(out) * Attention2 + out + self.fft_block9(out))
        
        Attention3 = self.SAM1(out) 
        out = F.relu(self.res_block10(out) * Attention3 + out + self.fft_block10(out))
        out = F.relu(self.res_block11(out) * Attention3 + out + self.fft_block11(out))
        out = F.relu(self.res_block12(out) * Attention3 + out + self.fft_block12(out))
        
        Attention4 = self.SAM1(out) 
        out = F.relu(self.res_block13(out) * Attention4 + out + self.fft_block13(out))
        out = F.relu(self.res_block14(out) * Attention4 + out + self.fft_block14(out))
        out = F.relu(self.res_block15(out) * Attention4 + out + self.fft_block15(out))
        
        
        out = F.relu(self.res_block16(out) + out + self.fft_block16(out))
        out = F.relu(self.res_block17(out) + out + self.fft_block17(out))
       
    
        out = self.conv_out(out)
        

        return Attention4,out

class Generator(nn.Module):
    def __init__(self, gpu_ids):
        super().__init__()
        self.gpu_ids = gpu_ids

        self.gen = nn.Sequential(OrderedDict([('gen', SPANet())]))

        self.gen.apply(weights_init)

    def forward(self, x):
        
        if self.gpu_ids:
            return nn.parallel.data_parallel(self.gen, x, self.gpu_ids)
        else:
            return self.gen(x)
        
       # return self.gen(x)
      
