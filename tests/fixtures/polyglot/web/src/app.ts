import express from 'express';
import { CreateDto } from './dto';

export function createApp() {
  const app = express();
  app.post('/items', (req, res) => {
    const dto = new CreateDto(req.body.name);
    res.json(dto);
  });
  return app;
}
